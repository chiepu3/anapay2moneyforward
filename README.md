# anapay2moneyforward (改) - デビットカード対応版

このスクリプトは、ANA Payおよびデビットカード（住信SBIネット銀行を想定）の利用通知メールから支払い情報を抽出し、Googleスプレッドシートを経由してマネーフォワードに自動登録するものです。元々ANA Pay専用だったものをフォークし、デビットカード利用にも対応するよう拡張しました。
このスクリプトは、標準のマネーフォワードME（moneyforward.com）のログインプロセスに対応するように更新されています。

## 主な機能

*   ANA Pay利用通知メールの解析とMoney Forwardへの登録
*   デビットカード利用通知メール（住信SBIネット銀行のフォーマットを想定）の解析とMoney Forwardへの登録
*   Googleスプレッドシートを中間データストアとして使用
*   設定可能なデビットカード資産名

## 環境構築

1.  **リポジトリのクローンまたはダウンロード**
2.  **Python環境の準備 (例: venv)**
    ```bash
    python3 -m venv env
    source env/bin/activate  # macOS / Linux
    # env\Scriptsctivate  # Windows
    ```
3.  **必要なライブラリのインストール**
    ```bash
    pip install -r requirements.txt
    ```
4.  **環境変数の設定**
    *   プロジェクトのルートディレクトリに `.env` ファイルを作成し、以下の情報を記述します。
        ```dotenv
        EMAIL="マネーフォワードのログインメールアドレス"
        PASSWORD="マネーフォワードのログインパスワード"
        SHEET_ID="ここに取得したスプレッドシートIDを貼り付け"
        DEBIT_CARD_ASSET_NAME="Money Forward上のデビットカードの資産名"
        ```
    *   `DEBIT_CARD_ASSET_NAME` はオプションです。設定しない場合、デフォルト値「住信SBIネット銀行 V NEO支店」が使用されます。Money Forwardに登録されているご自身のデビットカードの正確な資産名（例：「〇〇銀行デビット」、「デビットカード（XXXX）」など）を指定してください。

## APIとスプレッドシートの準備

### 1. GmailとGoogle Sheets APIの有効化

*   以下のページも参考にして、GoogleのAPIを使えるように設定してください。
    *   [Python クイックスタート | Gmail | Google for Developers](https://developers.google.com/gmail/api/quickstart/python?hl=ja)
*   手順概要:
    1.  Google Cloudコンソールでプロジェクトを作成（または既存プロジェクトを選択）。
    2.  プロジェクトで「Gmail API」と「Google Sheets API」を有効にします。
    3.  OAuth同意画面を設定します（アプリケーションの種類、アプリ名、ユーザサポートメール、承認済みドメインなど）。スコープの追加は不要です。
    4.  テストユーザーとして、このスクリプトで使用するGoogleアカウント（Gmailとスプレッドシートにアクセスするアカウント）を追加します。
    5.  「認証情報」画面で「認証情報を作成」→「OAuthクライアントID」を選択。
    6.  アプリケーションの種類で「デスクトップアプリ」を選択し、名前を付けます。
    7.  作成されたクライアントIDのリストから、作成した認証情報を選択し、「JSONをダウンロード」をクリックして `credentials.json` という名前でプロジェクトのルートディレクトリに保存します。

### 2. 初回認証 (token.jsonの生成)

*   `credentials.json` を配置した後、以下のコマンドを実行します。
    ```bash
    python quickstart.py
    ```
*   ブラウザが起動し、Googleアカウントへのアクセス許可を求められます。許可すると、プロジェクトのルートディレクトリに `token.json` が生成されます。これはスクリプトがGoogle APIにアクセスするために必要なトークンです。

### 3. Googleスプレッドシートの準備

1.  **新しいスプレッドシートの作成:** Googleドライブで新しいスプレッドシートを作成します。
2.  **スプレッドシートIDの取得と設定:**
    *   作成したスプレッドシートのURLは `https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit#gid=0` のような形式になっています。この `SPREADSHEET_ID` の部分をコピーします。
    *   コピーした `SPREADSHEET_ID` を、先に説明した `.env` ファイル内の `SHEET_ID` 環境変数に設定します。
3.  **シートの作成とヘッダー設定:**
    *   **`ANAPay` シート:**
        *   1つ目のシート名を `ANAPay` に変更します。
        *   1行目に以下のヘッダーを順番通りに入力します。
            `email_date | date_of_use | amount | store | transaction_type | asset_name | mf`
    *   **`ANAPayStore` シート (任意だが推奨):**
        *   2つ目のシートを作成し、名前を `ANAPayStore` に変更します。
        *   1行目に以下のヘッダーを入力します。
            `store | 大項目 | 中項目 | 店名`
        *   2行目以降に、店舗名とMoney Forwardのカテゴリの対応を入力しておくと、自動でカテゴリ分類が設定されます。`store`列の文字列はメールから抽出される店舗名と完全に一致させてください。

## スクリプトの実行

```bash
python anapay2mf.py
```
スクリプトは以下の処理を実行します。
1.  GmailからANA Payおよびデビットカードの未処理メールを検索。
2.  メール内容を解析し、`ANAPay` スプレッドシートに追記。
3.  `ANAPay` スプレッドシートの未処理取引をMoney Forwardに登録し、処理済みのマーク (`mf`列に "done") を付ける。

## 単体テストの実行

追加されたメール解析ロジックの単体テストは、以下のコマンドで実行できます。
```bash
python test_anapay2mf.py
```

## 注意事項

*   このスクリプトはブラウザ操作 (Heliumライブラリを使用) を伴うため、実行環境の状況によっては動作が不安定になる可能性があります。
*   Money ForwardのUIが変更された場合、スクリプトの修正が必要になることがあります。
*   パスワードなどの機密情報は `.env` ファイルで管理し、リポジトリに直接コミットしないように注意してください。
*   デビットカードのメール解析は、住信SBIネット銀行の特定のフォーマットを前提としています。他の銀行のメールに対応する場合は、`get_debit_card_mail_info` 関数の修正が必要です。
