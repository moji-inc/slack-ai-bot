# ChatGPT in Slack - Koyeb デプロイガイド

このガイドでは、ChatGPT in Slackアプリケーションを[Koyeb](https://www.koyeb.com/)にデプロイする方法を説明します。Koyebはサーバーレスプラットフォームで、DockerコンテナやWebSocketをサポートしているため、このSlackアプリケーションの実行に最適です。

## 前提条件

- [Koyeb](https://www.koyeb.com/)アカウント
- Slackアプリの認証情報（SLACK_BOT_TOKENとSLACK_APP_TOKEN）
- OpenAIのAPIキー（OPENAI_API_KEY）

## デプロイ手順

### 1. Slackアプリの設定

1. [api.slack.com/apps](https://api.slack.com/apps)にアクセスし、新しいアプリを作成します。
2. `manifest-dev.yml`を使用してアプリを設定します。
   - **重要**: `socket_mode_enabled: true`を確認してください。
3. アプリをワークスペースにインストールします。
4. 「Basic Information」ページから`App Token`（`xapp-`で始まる）を取得します。
5. 「OAuth & Permissions」ページから`Bot User OAuth Token`（`xoxb-`で始まる）を取得します。

### 2. Koyebでのデータベース作成

1. Koyebダッシュボードにログインします。
2. 「Database」セクションに移動し、「Create Database」をクリックします。
3. 以下の設定でPostgreSQLデータベースを作成します：
   - 名前: `slack-chatgpt-db`（任意）
   - タイプ: PostgreSQL
   - バージョン: 15（または最新バージョン）
   - リージョン: 東京（または最寄りのリージョン）
4. データベースが作成されたら、接続情報を取得します（後で環境変数として使用）。

### 3. アプリケーションのデプロイ

#### Option A: GitHubリポジトリからデプロイ

1. このリポジトリをフォークして自分のGitHubアカウントに追加します。
2. Koyebダッシュボードで「Create App」をクリックします。
3. 「GitHub」を選択し、フォークしたリポジトリを接続します。
4. 以下の設定を行います：
   - 名前: `slack-chatgpt-bot`（任意）
   - リージョン: 東京（または最寄りのリージョン）
   - インスタンスタイプ: 基本的なもの（Micro）で開始
   - ビルド設定:
     - ビルドメソッド: Dockerfile
     - Dockerfileパス: `Dockerfile.koyeb`
   - 環境変数:
     - `SLACK_APP_TOKEN`: Slackアプリトークン（xapp-から始まる）
     - `SLACK_BOT_TOKEN`: Slackボットトークン（xoxb-から始まる）
     - `OPENAI_API_KEY`: OpenAI APIキー（sk-から始まる）
     - `DATABASE_URL`: ステップ2で作成したデータベースの接続URL
     - その他必要な設定（オプション）

#### Option B: Dockerイメージを直接ビルド・プッシュ

1. ローカルでDockerイメージをビルドします：
```bash
docker build -f Dockerfile.koyeb -t your-org/chatgpt-in-slack-koyeb .
```

2. イメージをDockerレジストリにプッシュします（DockerHubまたはKoyebのプライベートレジストリ）：
```bash
docker tag your-org/chatgpt-in-slack-koyeb your-registry/chatgpt-in-slack-koyeb:latest
docker push your-registry/chatgpt-in-slack-koyeb:latest
```

3. Koyebダッシュボードで「Create App」をクリックし、「Docker Image」を選択します。
4. 上記のDocker画像URLを指定し、環境変数を設定します（Option Aと同様）。

### 4. アプリケーションの設定

アプリケーションがデプロイされたら、以下の設定を確認/調整します：

1. **自動スケーリング設定**:
   - 最小インスタンス数を1に設定（常時接続を維持するため）
   - アイドルタイムアウトを無効化

2. **ヘルスチェック**:
   - パス: `/` または `/health`
   - ポート: 8080
   - プロトコル: HTTP

3. **リソース割り当て**:
   - メモリ: 最低512MB
   - CPU: 最低0.5コア

## 動作確認

1. デプロイが完了したら、Slackワークスペースでボットをメンションして動作確認します。
2. `@ChatGPT Hello, are you working?`のようにメッセージを送信します。
3. ボットが応答すれば、デプロイは成功です。

## トラブルシューティング

1. **接続エラー**: 環境変数（特にトークン）が正しく設定されているか確認します。
2. **データベース接続エラー**: DATABASE_URLが正しいフォーマットかつアクセス可能か確認します。
3. **ログの確認**: Koyebダッシュボードでアプリのログを確認し、エラーメッセージを特定します。

## その他の設定

- **カスタムドメイン**: アプリに独自ドメインを設定したい場合は、Koyebのカスタムドメイン機能を使用してください（ただしSocket Mode使用時は不要）。
- **環境変数の追加**: 必要に応じて以下のオプション環境変数を設定できます：
  - `OPENAI_MODEL`: デフォルトのモデル（例: "gpt-4o"）
  - `OPENAI_TEMPERATURE`: 温度設定（例: "1"）
  - `USE_SLACK_LANGUAGE`: Slackの言語に合わせるか（"true"または"false"）

## メンテナンス

- 定期的にKoyebダッシュボードでパフォーマンスとログを確認してください。
- 必要に応じてリソース割り当てを調整してください。

## サポート

問題が発生した場合は、GitHubリポジトリでIssueを作成するか、Koyebサポートにお問い合わせください。 