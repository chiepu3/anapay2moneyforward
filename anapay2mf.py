"""
ANA Payの情報をメールから取得してスプレッドシートに書き込む

それからスプレッドシートの情報を元に、Money Fowardに情報を書き込む
"""

import base64
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import gspread
import helium
from dateutil import parser
from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import quickstart

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Google Spreadsheet ID and Sheet name
SHEET_ID = "143Ewai1jFlt4d4msZI8fXersf2IErrzTQfFjjrwzOwM"
SHEET_NAME = "ANAPay"

MF_URL = "https://moneyforward.com/cf"

format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(format=format, level=logging.INFO)

load_dotenv()

# Check for DEBIT_CARD_ASSET_NAME environment variable
DEBIT_CARD_ASSET_NAME_DEFAULT = "住信SBIネット銀行 V NEO支店"
debit_card_asset_name = os.getenv('DEBIT_CARD_ASSET_NAME', DEBIT_CARD_ASSET_NAME_DEFAULT)
if debit_card_asset_name == DEBIT_CARD_ASSET_NAME_DEFAULT and not os.getenv('DEBIT_CARD_ASSET_NAME'):
    logging.info(f"DEBIT_CARD_ASSET_NAME is not set, using default: {DEBIT_CARD_ASSET_NAME_DEFAULT}")
else:
    logging.info(f"Using DEBIT_CARD_ASSET_NAME: {debit_card_asset_name}")


@dataclass
class ANAPay:
    """ANA Pay information"""

    email_date: datetime = None
    date_of_use: datetime = None
    amount: int = 0
    store: str = ""
    transaction_type: str = ""
    asset_name: str = ""

    def values(self) -> tuple[str, str, str, str, str, str]:
        """return tuple of values for spreadsheet"""
        return (
            self.email_date_str,
            self.date_of_use_str,
            self.amount,
            self.store,
            self.transaction_type,
            self.asset_name,
        )

    @property
    def email_date_str(self) -> str:
        return f"{self.email_date:%Y-%m-%d %H:%M:%S}"

    @property
    def date_of_use_str(self) -> str:
        return f"{self.date_of_use:%Y-%m-%d %H:%M:%S}"


def get_mail_info(res: dict) -> ANAPay | None:
    """
    1件のメールからANA Payの利用情報を取得して返す
    """
    ana_pay = ANAPay(transaction_type="ANAPay")
    for header in res["payload"]["headers"]:
        if header["name"] == "Date":
            date_str = header["value"].replace(" +0900 (JST)", "")
            ana_pay.email_date = parser.parse(date_str)

    # 本文から日時、金額、店舗を取り出す
    # ご利用日時：2023-06-28 22:46:19
    # ご利用金額：44,308円
    # ご利用店舗：SMOKEBEERFACTORY OTSUKATE
    data = res["payload"]["body"]["data"]
    body = base64.urlsafe_b64decode(data).decode()
    for line in body.splitlines():
        if line.startswith("ご利用"):
            key, value = line.split("：")
            if key == "ご利用日時":
                ana_pay.date_of_use = parser.parse(value)
            elif key == "ご利用金額":
                ana_pay.amount = int(value.replace(",", "").replace("円", ""))
            elif key == "ご利用店舗":
                ana_pay.store = value
    return ana_pay


def get_debit_card_mail_info(res: dict) -> ANAPay | None:
    """
    1件のメールからデビットカードの利用情報を取得して返す
    """
    ana_pay = ANAPay(transaction_type="DebitCard")
    for header in res["payload"]["headers"]:
        if header["name"] == "Date":
            date_str = header["value"].replace(" +0900 (JST)", "")
            ana_pay.email_date = parser.parse(date_str)

    # 本文から日時、金額、店舗を取り出す
    # ご利用日時：2023/11/20 12:00:00
    # ご利用加盟店：テスト加盟店
    # 引落金額：1,234.00
    data = res["payload"]["body"]["data"]
    body = base64.urlsafe_b64decode(data).decode()
    for line in body.splitlines():
        if line.startswith("ご利用日時："):
            _, value = line.split("：", 1)
            ana_pay.date_of_use = parser.parse(value)
        elif line.startswith("ご利用加盟店："):
            _, value = line.split("：", 1)
            ana_pay.store = value
        elif line.startswith("引落金額："):
            _, value = line.split("：", 1)
            ana_pay.amount = int(float(value.replace(",", "")))

    if ana_pay.date_of_use and ana_pay.store and ana_pay.amount > 0:
        return ana_pay
    return None


def get_transaction_emails(after: str) -> list[ANAPay]:
    """
    GmailからANA Payとデビットカードの利用履歴を取得する
    """
    transaction_list = []
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    service = build("gmail", "v1", credentials=creds)

    queries = {
        "anapay": f"from:payinfo@121.ana.co.jp subject:ご利用のお知らせ after:{after}",
        "debit": f"from:post_master@netbk.co.jp subject:デビットカードのご利用がありました。 after:{after}",
    }

    all_messages_ids = []

    for mail_type, query in queries.items():
        logging.info(f"Fetching {mail_type} emails with query: {query}")
        results = service.users().messages().list(userId="me", q=query).execute()
        messages_for_type = results.get("messages", [])
        for msg in messages_for_type:
            # メッセージIDにメールタイプを付加して後で識別できるようにする
            all_messages_ids.append({"id": msg["id"], "type": mail_type, "message": msg}) # messageを後でソートに使う
        logging.info(f"Found {len(messages_for_type)} {mail_type} emails.")

    # Gmailから取得するメッセージは最新のものが先頭に来るため、
    # reversedせずにそのまま処理し、最後にリスト全体を逆順にする方がメールの時系列順になる
    # ただし、異なる種類のメールが混在する場合、Gmailの検索結果の順序が必ずしも送信日時順とは限らないため、
    # email_dateでソートするのが最も確実。ここではまずIDで取得し、後でソートする。

    processed_emails = []
    for msg_info in all_messages_ids:
        res = service.users().messages().get(userId="me", id=msg_info["id"]).execute()
        
        transaction_info = None
        mail_type = msg_info["type"]

        if mail_type == "anapay":
            transaction_info = get_mail_info(res)
            if transaction_info:
                transaction_info.asset_name = "ANA Pay"
        elif mail_type == "debit":
            transaction_info = get_debit_card_mail_info(res)
            if transaction_info:
                # 環境変数 DEBIT_CARD_ASSET_NAME を使用
                transaction_info.asset_name = debit_card_asset_name
        
        if transaction_info and transaction_info.email_date: # email_dateがNoneでないことを確認
            processed_emails.append(transaction_info)
        elif transaction_info:
            logging.warning(f"Transaction info for message id {msg_info['id']} lacks email_date. Skipping sort for this item initially.")
            # email_dateがない場合でもリストには追加し、後処理で対応するかログで警告
            processed_emails.append(transaction_info)


    # email_dateでソートする (降順、新しいものが先)
    # email_dateがNoneの可能性がある場合は適切に処理する
    processed_emails.sort(key=lambda x: x.email_date if x.email_date else datetime.min, reverse=True)
    
    return processed_emails


def get_last_email_date(records: list[dict[str, str]]):
    """get last email date for gmail search"""
    after = "2023/06/28"
    if records:
        last_email_date = parser.parse(records[-1]["email_date"])
        after = f"{last_email_date:%Y/%m/%d}"
    return after


def gmail2spredsheet(worksheet):
    """gmailからANA Payの利用履歴を取得しスプレッドシートに書き込む"""
    # get all records from spreadsheet
    # worksheet.get_all_records() はヘッダー行をキーとした辞書のリストを返すため、
    # この呼び出しの前に実際のヘッダー行をチェックするのが適切です。
    # しかし、get_all_records()の後にヘッダーチェックを行うように指示されているため、
    # ここでは一度呼び出し、その後で再度ヘッダー行を取得してチェックします。
    # より効率的なのは、まずrow_values(1)でヘッダーをチェックし、問題なければ
    # get_all_records()を呼び出すことです。
    records = worksheet.get_all_records()
    logging.info("Records in spreadsheet: %d", len(records))

    expected_header = ["email_date", "date_of_use", "amount", "store", "transaction_type", "asset_name", "mf"]
    actual_header = []
    try:
        actual_header = worksheet.row_values(1) # 1行目の値を取得
    except Exception as e:
        logging.warning(f"Could not retrieve header row from spreadsheet: {e}")

    if actual_header: # ヘッダー行が取得できた場合のみ比較
        if actual_header != expected_header:
            logging.warning(
                "Spreadsheet header does not match expected header. "
                f"Expected: {expected_header}, Found: {actual_header}. "
                "Please ensure the header is set correctly as per the documentation."
            )
    else: # ヘッダー行が空または取得できなかった場合
        logging.warning(
            "Spreadsheet header could not be read or is empty. "
            f"Please ensure the header is set correctly to: {expected_header}"
        )

    # get last day from records
    after = get_last_email_date(records)
    logging.info("Last day on spreadsheet: %s", after)
    email_date_set = set(parser.parse(r["email_date"]) for r in records)

    # get transaction emails from Gmail
    transaction_emails = get_transaction_emails(after)
    logging.info("Transaction emails fetched: %d", len(transaction_emails))

    # add transaction record to spreadsheet
    added_count = 0
    for transaction in transaction_emails:
        # メールの日付が存在しない場合はレコードを追加
        if transaction.email_date not in email_date_set:
            worksheet.append_row(transaction.values(), value_input_option="USER_ENTERED")
            added_count += 1
            logging.info("Record added to spreadsheet: %s", transaction.values())
    logging.info("Records added to spreadsheet: %d", added_count)


def login_mf():
    """login moneyforward ME with two-step process"""

    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    login_url = "https://moneyforward.com/users/sign_in"

    logging.info(f"Navigating to Money Forward login page: {login_url}")
    
    helium.start_firefox() 
    helium.go_to(login_url)

    logging.info(f"Attempting to login with email: {email}")

    try:
        # Step 1: Enter email and click the first submit button
        email_field = helium.TextField(name="mfid_user[email]")
        helium.wait_until(email_field.exists, timeout_secs=15) 
        helium.write(email, into=email_field)

        # Attempt to click "Keep me logged in" checkbox on the email page
        try:
            logging.info("Attempting to find and click '次回から自動的にログインする' checkbox on email page.")
            # Primary selector based on visible text, as confirmed by manual check
            remember_me_checkbox_by_text = helium.CheckBox("次回から自動的にログインする")
            # Fallback selector by name attribute
            remember_me_checkbox_by_name = helium.CheckBox(name="mfid_user[session_remember_me]")

            checkbox_found_and_handled = False
            if remember_me_checkbox_by_text.exists():
                logging.info(f"Found 'Keep me logged in' checkbox by text: {remember_me_checkbox_by_text}")
                if not remember_me_checkbox_by_text.is_checked():
                    logging.info("Clicking 'Keep me logged in' checkbox (found by text).")
                    helium.click(remember_me_checkbox_by_text)
                else:
                    logging.info("'Keep me logged in' checkbox (found by text) is already checked.")
                checkbox_found_and_handled = True
            elif remember_me_checkbox_by_name.exists():
                logging.info(f"Found 'Keep me logged in' checkbox by name attribute: {remember_me_checkbox_by_name}")
                if not remember_me_checkbox_by_name.is_checked():
                    logging.info("Clicking 'Keep me logged in' checkbox (found by name).")
                    helium.click(remember_me_checkbox_by_name)
                else:
                    logging.info("'Keep me logged in' checkbox (found by name) is already checked.")
                checkbox_found_and_handled = True
            
            if not checkbox_found_and_handled:
                logging.info("'Keep me logged in' ('次回から自動的にログインする') checkbox not found on the email page with given selectors.")
                
        except Exception as e:
            logging.warning(f"An error occurred while trying to interact with 'Keep me logged in' checkbox on email page: {e}")
        
        # Refined selectors for the first login button (after email submission)
        first_login_button_selectors = [
            helium.S('input[type="submit"][value="上記に同意してメールアドレスでログイン"]'),
            helium.S('input[type="submit"][value="同意してメールアドレスを登録"]'),
            helium.S('input[type="submit"].btn.btn-primary.btn-block') # Fallback
        ]
        
        clicked_first_button = False
        for selector in first_login_button_selectors:
            if selector.exists():
                logging.info(f"Found first login button with selector: {selector}")
                helium.click(selector)
                clicked_first_button = True
                break
        if not clicked_first_button:
            logging.error("Could not find the first login button (after email submission).")
            raise Exception("First login button not found after email submission.")

        logging.info("Email submitted. Waiting for password page.")
        
        # Step 2: Enter password and click the second submit button
        password_field = helium.TextField(name="mfid_user[password]")
        helium.wait_until(password_field.exists, timeout_secs=15) # Increased timeout
        helium.write(password, into=password_field)
        
        # Refined selectors for the second login button (on the password page)
        second_login_button_selectors = [
            helium.S('input[type="submit"][value="ログインする"].btn.btn-primary.btn-block'),
            helium.S('input[type="submit"][value="ログインする"]') # Fallback
        ]
        
        clicked_second_button = False
        for selector in second_login_button_selectors:
            if selector.exists():
                logging.info(f"Found second login button with selector: {selector}")
                helium.click(selector)
                clicked_second_button = True
                break
        if not clicked_second_button:
            logging.error("Could not find the second login button (after password submission).")
            raise Exception("Second login button not found after password submission.")

        logging.info("Password submitted. Waiting for main page content (手入力 button).")
        
        # Wait for an element that indicates successful login and page load to https://moneyforward.com/cf
        # (where the "手入力" button is expected).
        helium.wait_until(helium.Button("手入力").exists, timeout_secs=30) # Kept 30s for final load
        logging.info("Successfully logged into Money Forward ME and found '手入力' button.")

    except Exception as e:
        logging.error(f"An error occurred during login: {e}")
        logging.info("Browser was running or an error occurred during login, attempting to close it.")
        helium.kill_browser() # Ensure browser is closed on error
        raise # Re-raise the exception so the main script knows login failed.


def add_mf_record(dt: datetime, amount: int, store: str, asset_name: str, store_info: dict | None):
    """
    add record to moneyforward
    """

    # https://selenium-python-helium.readthedocs.io/en/latest/api.html
    helium.click("手入力")
    # breakpoint()
    helium.write(f"{dt:%Y/%m/%d}", into="日付")
    helium.click("日付")

    helium.write(amount, into="支出金額")
    asset_combobox = helium.find_all(helium.ComboBox())[0] # 資産選択のコンボボックス
    
    # asset_name に基づいて資産を選択
    found_asset = False
    for option_text in asset_combobox.options:
        if option_text.strip() == asset_name.strip():
            helium.select(asset_combobox, option_text)
            found_asset = True
            break
    if not found_asset:
        logging.warning(f"Asset '{asset_name}' not found in Money Forward. Using default or first available.")
        # ここでデフォルトの資産を選択するか、エラー処理を行う
        # 例: helium.select(asset_combobox, asset_combobox.options[0]) # 最初のオプションを選択

    if store_info:
        category = helium.find_all(helium.Link("未分類"))[0]
        l_category = helium.find_all(helium.S("#js-large-category-selected"))[0]
        helium.click(l_category)
        helium.click(store_info["大項目"])

        m_category = helium.find_all(helium.S("#js-middle-category-selected"))[0]
        helium.click(m_category)
        helium.click(store_info["中項目"])

        helium.write(store_info["店名"], into="内容をご入力下さい(任意)")
    else:
        helium.write(store, into="内容をご入力下さい(任意)")

    helium.click("保存する")
    logging.info(f"Record added to moneyforward: {dt:%Y/%m/%d}, {amount}, {store}")

    helium.wait_until(helium.Button("続けて入力する").exists)
    helium.click("続けて入力する")


def spreadsheet2mf(worksheet, store_dict: dict[str, dict[str, str]]) -> None:
    """スプレッドシートからmoneyfowardに書き込む"""

    records = worksheet.get_all_records()

    # すべてmoneyforwardに登録済みならなにもしない
    if all(record["mf"] == "done" for record in records):
        return

    login_mf()  # login to moneyfoward
    added = 0
    for count, record in enumerate(records):
        if record.get("mf") != "done": # "mf" キーが存在しない場合も考慮
            date_of_use = parser.parse(record["date_of_use"])
            amount = int(record["amount"])
            store = record["store"]
            # スプレッドシートから asset_name を取得
            asset_name_from_sheet = record.get("asset_name")
            if not asset_name_from_sheet:
                logging.error(f"Asset name is missing in spreadsheet for record: {record}. Skipping this record.")
                continue

            add_mf_record(date_of_use, amount, store, asset_name_from_sheet, store_dict.get(store))

            # update spread sheets for "done" message
            # "mf" カラムのインデックスを特定する。G列 (7番目) と仮定。
            # ヘッダーに基づいて動的に列インデックスを見つける方が堅牢。
            # ここでは、'mf' が5番目のカラム(E列)であるという前提を継続
            # しかし、新しいカラムが追加されたため、'mf'の位置が変わっている可能性がある。
            # ヘッダーに基づいて "mf" 列のインデックスを見つける
            header_values = worksheet.row_values(1) # 1行目をヘッダーとして取得
            mf_column_index = 0
            try:
                mf_column_index = header_values.index("mf") + 1 # 1-based index
            except ValueError:
                logging.error("'mf' column not found in spreadsheet header. Cannot update status.")
                # 'mf'列が見つからない場合、エラーとして処理するか、デフォルトの列を使う
                # ここではエラーログを出力し、更新をスキップする可能性がある
                # あるいは、固定のインデックスを使う (例: 5) ただし、これは非推奨
                mf_column_index = 5 # 従来のE列を仮定 (非推奨)

            if mf_column_index: # mf列が見つかった場合のみ更新
                worksheet.update_cell(count + 2, mf_column_index, "done")
            added += 1
    helium.kill_browser()

    logging.info(f"Records added to moneyforward: {added}")


def main():
    try:
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        service = build('gmail', 'v1', credentials=creds)
        results = service.users().labels().list(userId='me').execute()
    except RefreshError:
        # recreate token
        Path("token.json").unlink(missing_ok=True)
        quickstart.main()

    gc = gspread.oauth(
        credentials_filename="credentials.json", authorized_user_filename="token.json"
    )
    sheet = gc.open_by_key(SHEET_ID)
    anapay_sheet = sheet.worksheet("ANAPay")
    store_sheet = sheet.worksheet("ANAPayStore")
    store_dict = {store["store"]: store for store in store_sheet.get_all_records()}

    gmail2spredsheet(anapay_sheet)
    spreadsheet2mf(anapay_sheet, store_dict)


if __name__ == "__main__":
    main()
