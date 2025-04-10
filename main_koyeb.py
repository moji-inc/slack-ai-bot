import json
import logging
import os
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

from slack_bolt import App, BoltContext
from slack_sdk.web import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.bolt_listeners import register_listeners, before_authorize
from app.env import (
    USE_SLACK_LANGUAGE,
    SLACK_APP_LOG_LEVEL,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    OPENAI_API_TYPE,
    OPENAI_API_BASE,
    OPENAI_API_VERSION,
    OPENAI_DEPLOYMENT_ID,
    OPENAI_FUNCTION_CALL_MODULE_NAME,
    OPENAI_ORG_ID,
    OPENAI_IMAGE_GENERATION_MODEL,
)
from app.slack_ui import (
    build_home_tab,
    DEFAULT_HOME_TAB_MESSAGE,
    build_configure_modal,
)
from app.i18n import translate
from openai import OpenAI

# データベース接続
DATABASE_URL = os.environ.get("DATABASE_URL")

# インメモリストレージのフォールバック
in_memory_storage = {}

# データベースのセットアップ
def setup_database():
    try:
        if not DATABASE_URL:
            logging.error("DATABASE_URL is not set.")
            raise Exception("DATABASE_URL environment variable is required")
            
        # URLパースを使用して、接続パラメータを明示的に設定
        params = {
            "dbname": DATABASE_URL.split("/")[-1],
            "user": DATABASE_URL.split("://")[1].split(":")[0],
            "password": DATABASE_URL.split(":")[2].split("@")[0],
            "host": DATABASE_URL.split("@")[1].split("/")[0],
            "port": "5432"
        }
        
        logging.info(f"Connecting to database at {params['host']}")
        conn = psycopg2.connect(**params)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS openai_configs (
                team_id TEXT PRIMARY KEY,
                config JSONB NOT NULL
            )
        """)
        conn.commit()
        cursor.close()
        conn.close()
        logging.info("Database setup completed")
    except Exception as e:
        logging.error(f"Failed to setup database: {e}")
        raise

# チームのOpenAI設定を保存
def save_openai_config(team_id, config):
    try:
        if not DATABASE_URL:
            logging.error("DATABASE_URL is not set.")
            return False
            
        # URLパースを使用して、接続パラメータを明示的に設定
        params = {
            "dbname": DATABASE_URL.split("/")[-1],
            "user": DATABASE_URL.split("://")[1].split(":")[0],
            "password": DATABASE_URL.split(":")[2].split("@")[0],
            "host": DATABASE_URL.split("@")[1].split("/")[0],
            "port": "5432"
        }
        
        conn = psycopg2.connect(**params)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO openai_configs (team_id, config)
            VALUES (%s, %s)
            ON CONFLICT (team_id) 
            DO UPDATE SET config = %s
        """, (team_id, Json(config), Json(config)))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Failed to save OpenAI config: {e}")
        return False

# チームのOpenAI設定を取得
def get_openai_config(team_id):
    try:
        if not DATABASE_URL:
            logging.error("DATABASE_URL is not set.")
            return None
            
        # URLパースを使用して、接続パラメータを明示的に設定
        params = {
            "dbname": DATABASE_URL.split("/")[-1],
            "user": DATABASE_URL.split("://")[1].split(":")[0],
            "password": DATABASE_URL.split(":")[2].split("@")[0],
            "host": DATABASE_URL.split("@")[1].split("/")[0],
            "port": "5432"
        }
        
        conn = psycopg2.connect(**params)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT config FROM openai_configs
            WHERE team_id = %s
        """, (team_id,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            return result[0]
        return None
    except Exception as e:
        logging.error(f"Failed to get OpenAI config: {e}")
        return None

# チームのOpenAI設定を削除
def delete_openai_config(team_id):
    try:
        if not DATABASE_URL:
            logging.error("DATABASE_URL is not set.")
            return False
            
        # URLパースを使用して、接続パラメータを明示的に設定
        params = {
            "dbname": DATABASE_URL.split("/")[-1],
            "user": DATABASE_URL.split("://")[1].split(":")[0],
            "password": DATABASE_URL.split(":")[2].split("@")[0],
            "host": DATABASE_URL.split("@")[1].split("/")[0],
            "port": "5432"
        }
        
        conn = psycopg2.connect(**params)
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM openai_configs
            WHERE team_id = %s
        """, (team_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Failed to delete OpenAI config: {e}")
        return False

def register_revocation_handlers(app: App):
    # アンインストールイベントとトークン取り消しを処理
    @app.event("tokens_revoked")
    def handle_tokens_revoked_events(
        event: dict,
        context: BoltContext,
        logger: logging.Logger,
    ):
        user_ids = event.get("tokens", {}).get("oauth", [])
        if len(user_ids) > 0:
            for user_id in user_ids:
                app.installation_store.delete_installation(
                    enterprise_id=context.enterprise_id,
                    team_id=context.team_id,
                    user_id=user_id,
                )
        bots = event.get("tokens", {}).get("bot", [])
        if len(bots) > 0:
            app.installation_store.delete_bot(
                enterprise_id=context.enterprise_id,
                team_id=context.team_id,
            )
            try:
                delete_openai_config(context.team_id)
            except Exception as e:
                logger.error(
                    f"Failed to delete an OpenAI auth key: (team_id: {context.team_id}, error: {e})"
                )

    @app.event("app_uninstalled")
    def handle_app_uninstalled_events(
        context: BoltContext,
        logger: logging.Logger,
    ):
        app.installation_store.delete_all(
            enterprise_id=context.enterprise_id,
            team_id=context.team_id,
        )
        try:
            delete_openai_config(context.team_id)
        except Exception as e:
            logger.error(
                f"Failed to delete an OpenAI auth key: (team_id: {context.team_id}, error: {e})"
            )

def main():
    # 環境変数の読み込み
    load_dotenv()
    
    # データベースのセットアップ
    setup_database()
    
    # ログの設定
    logging.basicConfig(format="%(asctime)s %(message)s", level=SLACK_APP_LOG_LEVEL)
    
    # アプリの初期化
    app = App(
        token=os.environ["SLACK_BOT_TOKEN"],
        before_authorize=before_authorize,
        process_before_response=True,
    )
    app.client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=2))
    
    # リスナーの登録
    register_listeners(app)
    register_revocation_handlers(app)
    
    # OpenAI設定のミドルウェア
    @app.middleware
    def set_db_openai_api_key(context: BoltContext, next_):
        config = get_openai_config(context.team_id)
        if config:
            context["OPENAI_API_KEY"] = config.get("api_key")
            context["OPENAI_MODEL"] = config.get("model")
            context["OPENAI_IMAGE_GENERATION_MODEL"] = config.get(
                "image_generation_model", OPENAI_IMAGE_GENERATION_MODEL
            )
            context["OPENAI_TEMPERATURE"] = config.get(
                "temperature", OPENAI_TEMPERATURE
            )
        else:
            # シングルワークスペースモードの場合は環境変数から読み込む
            context["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")
            context["OPENAI_MODEL"] = OPENAI_MODEL
            context["OPENAI_IMAGE_GENERATION_MODEL"] = OPENAI_IMAGE_GENERATION_MODEL
            context["OPENAI_TEMPERATURE"] = OPENAI_TEMPERATURE
            
        context["OPENAI_API_TYPE"] = OPENAI_API_TYPE
        context["OPENAI_API_BASE"] = OPENAI_API_BASE
        context["OPENAI_API_VERSION"] = OPENAI_API_VERSION
        context["OPENAI_DEPLOYMENT_ID"] = OPENAI_DEPLOYMENT_ID
        context["OPENAI_ORG_ID"] = OPENAI_ORG_ID
        context["OPENAI_FUNCTION_CALL_MODULE_NAME"] = OPENAI_FUNCTION_CALL_MODULE_NAME
        next_()
    
    # ホームタブの表示
    @app.event("app_home_opened")
    def render_home_tab(client: WebClient, context: BoltContext):
        message = DEFAULT_HOME_TAB_MESSAGE
        try:
            config = get_openai_config(context.team_id)
            if config:
                message = "This app is ready to use in this workspace :raised_hands:"
        except Exception:
            pass
            
        openai_api_key = context.get("OPENAI_API_KEY")
        client.views_publish(
            user_id=context.user_id,
            view=build_home_tab(
                openai_api_key=openai_api_key,
                context=context,
                message=message,
                single_workspace_mode=False,
            ),
        )
    
    # 設定モーダル
    @app.action("configure")
    def handle_configure_button(
        ack, body: dict, client: WebClient, context: BoltContext
    ):
        ack()
        client.views_open(
            trigger_id=body["trigger_id"],
            view=build_configure_modal(context),
        )
    
    def validate_api_key_registration(ack: Ack, view: dict, context: BoltContext):
        already_set_api_key = context.get("OPENAI_API_KEY")

        inputs = view["state"]["values"]
        api_key = inputs["api_key"]["input"]["value"]
        model = inputs["model"]["input"]["selected_option"]["value"]
        try:
            # APIキーが有効かどうか確認
            client = OpenAI(api_key=api_key)
            client.models.retrieve(model="gpt-3.5-turbo")
            try:
                # 指定されたモデルがAPIキーで使用可能か確認
                client.models.retrieve(model=model)
            except Exception:
                text = "This model is not yet available for this API key"
                if already_set_api_key is not None:
                    text = translate(
                        openai_api_key=already_set_api_key, context=context, text=text
                    )
                ack(
                    response_action="errors",
                    errors={"model": text},
                )
                return
            ack()
        except Exception:
            text = "This API key seems to be invalid"
            if already_set_api_key is not None:
                text = translate(
                    openai_api_key=already_set_api_key, context=context, text=text
                )
            ack(
                response_action="errors",
                errors={"api_key": text},
            )
    
    def save_api_key_registration(
        view: dict,
        logger: logging.Logger,
        context: BoltContext,
    ):
        inputs = view["state"]["values"]
        api_key = inputs["api_key"]["input"]["value"]
        model = inputs["model"]["input"]["selected_option"]["value"]
        try:
            client = OpenAI(api_key=api_key)
            client.models.retrieve(model=model)
            save_openai_config(
                context.team_id,
                {"api_key": api_key, "model": model}
            )
        except Exception as e:
            logger.exception(e)
    
    app.view("configure")(
        ack=validate_api_key_registration,
        lazy=[save_api_key_registration],
    )
    
    # 言語設定のミドルウェア
    if USE_SLACK_LANGUAGE is True:
        @app.middleware
        def set_locale(
            context: BoltContext,
            client: WebClient,
            logger: logging.Logger,
            next_,
        ):
            bot_scopes = context.authorize_result.bot_scopes
            if bot_scopes is not None and "users:read" in bot_scopes:
                user_id = context.actor_user_id or context.user_id
                try:
                    user_info = client.users_info(user=user_id, include_locale=True)
                    context["locale"] = user_info.get("user", {}).get("locale")
                except SlackApiError as e:
                    logger.debug(f"Failed to fetch user info due to {e}")
                    pass
            next_()
    
    # Socket Modeでアプリを起動
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()

if __name__ == "__main__":
    main() 