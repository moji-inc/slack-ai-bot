import json
import logging
import os
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv
import threading
import http.server
import socketserver

from slack_bolt import App, BoltContext
from slack_bolt.context.ack import Ack
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
DATABASE_HOST = os.environ.get("DATABASE_HOST")
DATABASE_USER = os.environ.get("DATABASE_USER")
DATABASE_PASSWORD = os.environ.get("DATABASE_PASSWORD")
DATABASE_NAME = os.environ.get("DATABASE_NAME")
DATABASE_PORT = os.environ.get("DATABASE_PORT", "5432")

# インメモリストレージのフォールバック
in_memory_storage = {}

# データベースのセットアップ
def setup_database():
    global in_memory_storage
    try:
        # 個別の環境変数から接続パラメータを構築
        if not DATABASE_URL and DATABASE_HOST and DATABASE_USER and DATABASE_PASSWORD and DATABASE_NAME:
            params = {
                "host": DATABASE_HOST,
                "user": DATABASE_USER,
                "password": DATABASE_PASSWORD,
                "dbname": DATABASE_NAME,
                "port": DATABASE_PORT
            }
            logging.info(f"Using individual database parameters to connect to {DATABASE_HOST}")
        elif DATABASE_URL:
            # URLパースを使用して、接続パラメータを明示的に設定
            try:
                params = {
                    "dbname": DATABASE_URL.split("/")[-1],
                    "user": DATABASE_URL.split("://")[1].split(":")[0],
                    "password": DATABASE_URL.split(":")[2].split("@")[0],
                    "host": DATABASE_URL.split("@")[1].split("/")[0],
                    "port": "5432"
                }
                logging.info(f"Using DATABASE_URL to connect to {params['host']}")
            except Exception as e:
                logging.warning(f"Failed to parse DATABASE_URL: {e}")
                if DATABASE_HOST and DATABASE_USER and DATABASE_PASSWORD and DATABASE_NAME:
                    params = {
                        "host": DATABASE_HOST,
                        "user": DATABASE_USER,
                        "password": DATABASE_PASSWORD,
                        "dbname": DATABASE_NAME,
                        "port": DATABASE_PORT
                    }
                    logging.info(f"Falling back to individual database parameters to connect to {DATABASE_HOST}")
                else:
                    logging.warning("No valid database connection information available. Using in-memory storage.")
                    in_memory_storage = {}
                    return
        else:
            logging.warning("No database connection information available. Using in-memory storage.")
            in_memory_storage = {}
            return
            
        # データベースに接続
        try:
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
            logging.info("Database setup completed successfully")
        except Exception as e:
            logging.warning(f"Failed to connect to database: {e}")
            logging.warning("Using in-memory storage as fallback")
            in_memory_storage = {}
    except Exception as e:
        logging.warning(f"Failed to setup database: {e}")
        logging.warning("Using in-memory storage as fallback")
        in_memory_storage = {}

# チームのOpenAI設定を保存
def save_openai_config(team_id, config):
    global in_memory_storage
    try:
        # 個別の環境変数から接続パラメータを構築
        if not DATABASE_URL and DATABASE_HOST and DATABASE_USER and DATABASE_PASSWORD and DATABASE_NAME:
            params = {
                "host": DATABASE_HOST,
                "user": DATABASE_USER,
                "password": DATABASE_PASSWORD,
                "dbname": DATABASE_NAME,
                "port": DATABASE_PORT
            }
        elif DATABASE_URL:
            # URLパースを使用して、接続パラメータを明示的に設定
            try:
                params = {
                    "dbname": DATABASE_URL.split("/")[-1],
                    "user": DATABASE_URL.split("://")[1].split(":")[0],
                    "password": DATABASE_URL.split(":")[2].split("@")[0],
                    "host": DATABASE_URL.split("@")[1].split("/")[0],
                    "port": "5432"
                }
            except Exception as e:
                logging.warning(f"Failed to parse DATABASE_URL: {e}")
                if DATABASE_HOST and DATABASE_USER and DATABASE_PASSWORD and DATABASE_NAME:
                    params = {
                        "host": DATABASE_HOST,
                        "user": DATABASE_USER,
                        "password": DATABASE_PASSWORD,
                        "dbname": DATABASE_NAME,
                        "port": DATABASE_PORT
                    }
                else:
                    logging.warning("No valid database connection information available. Using in-memory storage.")
                    in_memory_storage[team_id] = config
                    return True
        else:
            logging.warning("No database connection information available. Using in-memory storage.")
            in_memory_storage[team_id] = config
            return True
            
        # データベースに接続
        try:
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
            logging.warning(f"Failed to connect to database: {e}")
            logging.warning("Using in-memory storage")
            in_memory_storage[team_id] = config
            return True
    except Exception as e:
        logging.warning(f"Failed to save OpenAI config: {e}")
        logging.warning("Using in-memory storage")
        in_memory_storage[team_id] = config
        return True

# チームのOpenAI設定を取得
def get_openai_config(team_id):
    global in_memory_storage
    try:
        # 個別の環境変数から接続パラメータを構築
        if not DATABASE_URL and DATABASE_HOST and DATABASE_USER and DATABASE_PASSWORD and DATABASE_NAME:
            params = {
                "host": DATABASE_HOST,
                "user": DATABASE_USER,
                "password": DATABASE_PASSWORD,
                "dbname": DATABASE_NAME,
                "port": DATABASE_PORT
            }
        elif DATABASE_URL:
            # URLパースを使用して、接続パラメータを明示的に設定
            try:
                params = {
                    "dbname": DATABASE_URL.split("/")[-1],
                    "user": DATABASE_URL.split("://")[1].split(":")[0],
                    "password": DATABASE_URL.split(":")[2].split("@")[0],
                    "host": DATABASE_URL.split("@")[1].split("/")[0],
                    "port": "5432"
                }
            except Exception as e:
                logging.warning(f"Failed to parse DATABASE_URL: {e}")
                if DATABASE_HOST and DATABASE_USER and DATABASE_PASSWORD and DATABASE_NAME:
                    params = {
                        "host": DATABASE_HOST,
                        "user": DATABASE_USER,
                        "password": DATABASE_PASSWORD,
                        "dbname": DATABASE_NAME,
                        "port": DATABASE_PORT
                    }
                else:
                    logging.warning("No valid database connection information available. Using in-memory storage.")
                    return in_memory_storage.get(team_id)
        else:
            logging.warning("No database connection information available. Using in-memory storage.")
            return in_memory_storage.get(team_id)
            
        # データベースに接続
        try:
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
            logging.warning(f"Failed to connect to database: {e}")
            logging.warning("Using in-memory storage")
            return in_memory_storage.get(team_id)
    except Exception as e:
        logging.warning(f"Failed to get OpenAI config: {e}")
        logging.warning("Using in-memory storage")
        return in_memory_storage.get(team_id)

# チームのOpenAI設定を削除
def delete_openai_config(team_id):
    global in_memory_storage
    try:
        # 個別の環境変数から接続パラメータを構築
        if not DATABASE_URL and DATABASE_HOST and DATABASE_USER and DATABASE_PASSWORD and DATABASE_NAME:
            params = {
                "host": DATABASE_HOST,
                "user": DATABASE_USER,
                "password": DATABASE_PASSWORD,
                "dbname": DATABASE_NAME,
                "port": DATABASE_PORT
            }
        elif DATABASE_URL:
            # URLパースを使用して、接続パラメータを明示的に設定
            try:
                params = {
                    "dbname": DATABASE_URL.split("/")[-1],
                    "user": DATABASE_URL.split("://")[1].split(":")[0],
                    "password": DATABASE_URL.split(":")[2].split("@")[0],
                    "host": DATABASE_URL.split("@")[1].split("/")[0],
                    "port": "5432"
                }
            except Exception as e:
                logging.warning(f"Failed to parse DATABASE_URL: {e}")
                if DATABASE_HOST and DATABASE_USER and DATABASE_PASSWORD and DATABASE_NAME:
                    params = {
                        "host": DATABASE_HOST,
                        "user": DATABASE_USER,
                        "password": DATABASE_PASSWORD,
                        "dbname": DATABASE_NAME,
                        "port": DATABASE_PORT
                    }
                else:
                    logging.warning("No valid database connection information available. Using in-memory storage.")
                    if team_id in in_memory_storage:
                        del in_memory_storage[team_id]
                    return True
        else:
            logging.warning("No database connection information available. Using in-memory storage.")
            if team_id in in_memory_storage:
                del in_memory_storage[team_id]
            return True
            
        # データベースに接続
        try:
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
            logging.warning(f"Failed to connect to database: {e}")
            logging.warning("Using in-memory storage")
            if team_id in in_memory_storage:
                del in_memory_storage[team_id]
            return True
    except Exception as e:
        logging.warning(f"Failed to delete OpenAI config: {e}")
        logging.warning("Using in-memory storage")
        if team_id in in_memory_storage:
            del in_memory_storage[team_id]
        return True

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

def run_health_check_server():
    """ヘルスチェック用の簡易HTTPサーバーを起動"""
    port = int(os.environ.get("PORT", 8000))
    
    class HealthCheckHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
            
        def log_message(self, format, *args):
            # アクセスログを抑制
            return
    
    try:
        # TCPサーバーの作成
        with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
            logging.info(f"Health check server started at port {port}")
            httpd.serve_forever()
    except Exception as e:
        logging.error(f"Failed to start health check server: {e}")

def main():
    # ログの設定（最初に行う）
    logging.basicConfig(format="%(asctime)s %(message)s", level=SLACK_APP_LOG_LEVEL)
    
    # ヘルスチェック用のHTTPサーバーをバックグラウンドで起動
    health_thread = threading.Thread(target=run_health_check_server, daemon=True)
    health_thread.start()
    logging.info("Started health check server in background")
    
    # 環境変数のデバッグ（環境変数が設定されているか確認）
    logging.info("Checking environment variables...")
    env_keys = [key for key in os.environ.keys() if not key.startswith("PATH") and not key.startswith("LD_")]
    logging.info(f"Available environment variables: {', '.join(env_keys)}")
    
    # 環境変数の読み込み
    try:
        load_dotenv()
        logging.info("Loaded environment variables from .env file (if exists)")
    except Exception as e:
        logging.warning(f"Failed to load .env file: {e}, continuing with system environment variables")
    
    # 環境変数のチェック
    required_env_vars = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
    
    # 環境変数の値を確認（セキュリティのため一部を隠す）
    for var in required_env_vars:
        value = os.environ.get(var, "NOT_SET")
        if value != "NOT_SET":
            # セキュリティのため、トークンの最初と最後の数文字だけを表示
            masked_value = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
            logging.info(f"Environment variable {var} is set: {masked_value}")
        else:
            logging.error(f"Environment variable {var} is NOT set")
    
    if missing_vars:
        logging.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logging.error("Please set these environment variables in Koyeb")
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
    
    # データベースのセットアップ
    setup_database()
    
    # アプリの初期化
    try:
        # 直接環境変数から値を取得してログに出力（デバッグ用）
        slack_bot_token = os.environ.get("SLACK_BOT_TOKEN")
        if not slack_bot_token:
            logging.error("SLACK_BOT_TOKEN is still not available even after checks")
            # 緊急対応として環境変数を直接設定
            os.environ["SLACK_BOT_TOKEN"] = os.environ.get("SLACK_BOT_TOKEN_FALLBACK", "")
            slack_bot_token = os.environ.get("SLACK_BOT_TOKEN")
            if slack_bot_token:
                logging.info("Using SLACK_BOT_TOKEN_FALLBACK as SLACK_BOT_TOKEN")
        
        app = App(
            token=os.environ["SLACK_BOT_TOKEN"],
            before_authorize=before_authorize,
            process_before_response=True,
        )
        app.client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=2))
    except KeyError as e:
        logging.error(f"Failed to initialize Slack app due to missing environment variable: {e}")
        # 環境変数のキーを全て出力（デバッグ用）
        logging.error(f"Available environment keys: {list(os.environ.keys())}")
        raise
    
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