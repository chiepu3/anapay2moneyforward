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
import re # For 2FA code regex
from google.auth.transport.requests import Request as GoogleAuthRequest # For token refresh


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

# Google Spreadsheet ID and Sheet name
# SHEET_ID is now loaded from environment variables (Subtask 8)
SHEET_NAME = "ANAPay"

MF_URL = "https://moneyforward.com/cf" # Updated in Subtask 1

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

# Load SHEET_ID from environment variable (Subtask 8)
SHEET_ID = os.getenv("SHEET_ID")
if not SHEET_ID:
    logging.error("SHEET_ID is not set in the environment variables. Please set it in your .env file.")
else:
    logging.info(f"Using SHEET_ID from environment: {SHEET_ID}")


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

    data = res["payload"]["body"]["data"]
    body = base64.urlsafe_b64decode(data).decode()

    temp_date_of_use_str = None
    temp_store = None
    temp_amount_str = None

    for line in body.splitlines():
        line = line.strip() # 行頭・行末の空白を除去
        # 利用日時   ： YYYY/MM/DD HH:MM:SS
        match_date = re.match(r"利用日時\s*：\s*(.*)", line)
        if match_date:
            temp_date_of_use_str = match_date.group(1).strip()
            continue

        # 利用加盟店 ： ACTUAL STORE NAME
        match_store = re.match(r"利用加盟店\s*：\s*(.*)", line)
        if match_store:
            temp_store = match_store.group(1).strip()
            continue

        # 引落金額   ： N,NNN.NN
        match_amount = re.match(r"引落金額\s*：\s*(.*)", line)
        if match_amount:
            temp_amount_str = match_amount.group(1).strip()
            continue
    
    # Check if all necessary information was found
    if not temp_date_of_use_str or not temp_store or not temp_amount_str:
        logging.warning(
            f"Could not extract all required fields for debit card email. "
            f"Date: {temp_date_of_use_str}, Store: {temp_store}, Amount: {temp_amount_str}"
        )
        return None

    try:
        # Parse date_of_use
        ana_pay.date_of_use = datetime.strptime(temp_date_of_use_str, "%Y/%m/%d %H:%M:%S")
        
        # Set store
        ana_pay.store = temp_store
        
        # Parse amount
        ana_pay.amount = int(float(temp_amount_str.replace(",", "")))

    except ValueError as e:
        logging.error(f"Error parsing debit card mail info: {e}. Date: '{temp_date_of_use_str}', Amount: '{temp_amount_str}'")
        return None

    # Ensure amount is positive, though this might be redundant if it's always an expense
    if ana_pay.date_of_use and ana_pay.store and ana_pay.amount > 0:
        return ana_pay
    
    logging.warning(
        f"Debit card transaction info seems incomplete or invalid after parsing. "
        f"Date: {ana_pay.date_of_use}, Store: {ana_pay.store}, Amount: {ana_pay.amount}"
    )
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
        "debit": f"from:post_master@netbk.co.jp subject:(【デビットカード】ご利用のお知らせ(住信SBIネット銀行)) after:{after}",
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


def fetch_latest_mf_2fa_code_from_gmail(minutes_ago: int = 10) -> str | None:
    logging.info(f"Attempting to fetch Money Forward 2FA code from Gmail (last {minutes_ago}m).")
    creds = None
    token_path = Path('token.json')

    try:
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except Exception as e:
        logging.error(f"Error loading token.json: {e}. Will attempt quickstart.")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logging.info("Gmail token is expired, attempting to refresh.")
            try:
                creds.refresh(GoogleAuthRequest()) 
                with token_path.open('w') as token_file: 
                    token_file.write(creds.to_json())
                logging.info("Gmail token refreshed and saved.")
            except RefreshError as e_refresh:
                logging.error(f"Failed to refresh token: {e_refresh}. Attempting to re-run quickstart.")
                if token_path.exists():
                    token_path.unlink(missing_ok=True)
                try:
                    quickstart.main()
                    if token_path.exists():
                        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
                except Exception as e_qs_refresh:
                    logging.error(f"Failed to generate token via quickstart after refresh failure: {e_qs_refresh}")
                    return None
        else: 
            logging.info("No valid Gmail credentials or cannot refresh. Attempting to re-run quickstart.")
            if token_path.exists():
                token_path.unlink(missing_ok=True)
            try:
                quickstart.main()
                if token_path.exists():
                    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            except Exception as e_qs_final:
                logging.error(f"Failed to generate token via quickstart: {e_qs_final}")
                return None
    
    if not creds or not creds.valid: 
        logging.error("Unable to obtain valid Gmail credentials after all attempts.")
        return None

    try:
        service = build('gmail', 'v1', credentials=creds)
        
        query_parts = [
            "from:do_not_reply@moneyforward.com",
            "subject:(マネーフォワード ID メールによる追加認証)", 
            f"newer_than:{minutes_ago}m"
        ]
        query = " ".join(query_parts)
        logging.info(f"Searching Gmail with query: {query}")

        results = service.users().messages().list(userId='me', q=query, maxResults=1).execute()
        messages = results.get('messages', [])

        if not messages:
            logging.info("No 2FA email found matching the criteria.")
            return None

        msg_id = messages[0]['id']
        logging.info(f"Found potential 2FA email with ID: {msg_id}")
        msg_detail = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        
        payload = msg_detail.get('payload', {})
        data = None
        
        parts_to_check = [payload] 
        if 'parts' in payload:
            parts_to_check.extend(payload['parts'])
            for part_level1 in payload['parts']:
                if 'parts' in part_level1:
                    parts_to_check.extend(part_level1['parts'])

        for part_type_preference in ['text/plain', 'text/html']:
            for part_candidate in parts_to_check:
                if part_candidate.get('mimeType') == part_type_preference and 'body' in part_candidate and 'data' in part_candidate['body']:
                    data = part_candidate['body']['data']
                    logging.info(f"Found {part_type_preference} part for 2FA email.")
                    break
            if data:
                break
        
        if not data:
            logging.warning(f"Could not extract text body data (text/plain or text/html) from message ID {msg_id}.")
            return None

        body = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        
        match = re.search(r"こちらのコードを入力してログインを継続してください。\s*(\d{6})", body)
        
        if match:
            auth_code = match.group(1)
            logging.info(f"Extracted 2FA code: {auth_code}")
            return auth_code
        else:
            logging.warning(f"Could not find 2FA code in email body for message ID {msg_id}. Body (first 500 chars): {body[:500]}")
            return None

    except Exception as e:
        logging.error(f"Error fetching/processing 2FA code from Gmail: {e}")
        return None

def is_2fa_page_detected() -> bool:
    logging.info("Checking for 2FA page indicators...")
    heading_exists = helium.S("//h1[contains(text(),'追加認証のお願い')]").exists()
    if heading_exists:
        logging.info("Found 2FA page heading '追加認証のお願い'.")
    
    otp_input_exists = helium.S("input#email_otp").exists()
    if otp_input_exists:
        logging.info("Found OTP input field with id 'email_otp'.")

    if heading_exists and otp_input_exists:
        logging.info("Confirmed 2FA page based on heading and OTP input field.")
        return True
    
    logging.info("Did not find conclusive 2FA page indicators based on refined checks.")
    return False

def submit_2fa_code_to_page(auth_code: str):
    logging.info(f"Attempting to submit 2FA code '{auth_code}' to the page.")

    # Define selectors for OTP input field
    otp_input_field_selectors = [
        helium.S("#email_otp"),  # Preferred, using ID
        helium.TextField("000000")  # Fallback, using placeholder
    ]

    # Define selectors for submit button
    submit_button_selectors = [
        helium.Button("認証する"),  # Preferred, using text
        helium.S("#submitto")  # Fallback, using ID
    ]

    # Attempt to find and write to the OTP input field
    input_field_found_and_written = False
    for selector in otp_input_field_selectors:
        if selector.exists():
            logging.info(f"Found 2FA input field with selector: {selector}")
            try:
                # Attempt to click the field first to ensure focus, especially if using S() selector
                # This check ensures that we are dealing with an input element before trying to click it.
                if isinstance(selector, helium.S):
                    # Make sure get_driver() is available and the element is an input tag
                    try:
                        element = helium.get_driver().find_element(*selector.internal_selector_value)
                        if element.tag_name.lower() == 'input':
                            helium.click(selector)  # Click to focus
                        else:
                            logging.debug(f"Selector {selector} points to a non-input element '{element.tag_name}'. Skipping pre-click.")
                    except Exception as e_click_check:
                        # Handle cases where the element might not be immediately clickable or interactable
                        logging.warning(f"Could not perform pre-click check for selector {selector}: {e_click_check}. Proceeding to write.")
                
                helium.write(auth_code, into=selector)
                logging.info(f"Successfully wrote 2FA code into field using selector: {selector}")
                input_field_found_and_written = True
                break  # Exit loop once successfully written
            except Exception as e_write:
                logging.warning(f"Error writing to 2FA input field with selector {selector}: {e_write}. Trying next selector.")
        else:
            logging.debug(f"2FA input field selector not found: {selector}")

    if not input_field_found_and_written:
        logging.error("Could not find or write to the 2FA code input field on the page using available selectors.")
        raise Exception("2FA code input field not found or could not be written to.")

    # Attempt to find and click the submit button
    submit_button_found_and_clicked = False
    for selector in submit_button_selectors:
        if selector.exists():
            logging.info(f"Found 2FA submit button with selector: {selector}")
            try:
                helium.click(selector)
                logging.info(f"Successfully clicked 2FA submit button using selector: {selector}")
                submit_button_found_and_clicked = True
                break  # Exit loop once successfully clicked
            except Exception as e_click:
                logging.warning(f"Error clicking 2FA submit button with selector {selector}: {e_click}. Trying next selector.")
        else:
            logging.debug(f"2FA submit button selector not found: {selector}")

    if not submit_button_found_and_clicked:
        logging.error("Could not find or click the 2FA submit button on the page using available selectors.")
        raise Exception("2FA submit button not found or could not be clicked.")

    logging.info("2FA code submitted successfully.")


def handle_2fa_authentication():
    logging.info("Attempting to fetch 2FA code from Gmail for 2FA handling.")
    auth_code = fetch_latest_mf_2fa_code_from_gmail()

    if auth_code:
        logging.info(f"Retrieved 2FA code: {auth_code}")
        submit_2fa_code_to_page(auth_code) 
    else:
        logging.error("Failed to retrieve 2FA code from Gmail.")
        raise Exception("Could not retrieve 2FA code from Gmail.")

def login_mf():
    """login moneyforward ME with two-step process"""

    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    login_url = "https://moneyforward.com/users/sign_in" # From Subtask 4

    logging.info(f"Navigating to Money Forward login page: {login_url}")
    
    helium.start_firefox() 
    helium.go_to(login_url)

    logging.info(f"Attempting to login with email: {email}")

    try:
        # Step 1: Enter email (Subtask 9)
        logging.info("Waiting for email field 'メールアドレス' to exist.")
        helium.wait_until(helium.TextField("メールアドレス").exists, timeout_secs=15)
        logging.info("Writing email into 'メールアドレス' field.")
        helium.write(email, into=helium.TextField("メールアドレス"))

        # "Keep me logged in" checkbox (Subtask 5 / refined in Subtask 11)
        remember_me_checkbox_label = "次回から自動的にログインする"
        try:
            logging.info(f"Attempting to find and click '{remember_me_checkbox_label}' checkbox on email page.")
            checkbox_element = helium.CheckBox(remember_me_checkbox_label)
            if checkbox_element.exists():
                if not checkbox_element.is_checked():
                    logging.info(f"Clicking '{remember_me_checkbox_label}' checkbox.")
                    helium.click(checkbox_element)
                else:
                    logging.info(f"'{remember_me_checkbox_label}' checkbox already checked.")
            else:
                logging.info(f"'{remember_me_checkbox_label}' checkbox not found by label.")
        except Exception as e:
            logging.warning(f"An error occurred while trying to interact with '{remember_me_checkbox_label}' checkbox on email page: {e}")
        
        # First login button (Subtask 9)
        first_login_button_selectors = [
            helium.Button("ログインする"), 
            helium.S("#submitto"), 
            helium.S('input[type="submit"][value="上記に同意してメールアドレスでログイン"]'),
            helium.S('input[type="submit"][value="同意してメールアドレスを登録"]'),
            helium.S('input[type="submit"].btn.btn-primary.btn-block')
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

        # Wait for password page (Subtask 9)
        logging.info("Email submitted. Waiting for password page to load (expecting 'パスワード' field).")
        helium.wait_until(helium.TextField("パスワード").exists, timeout_secs=15) 
        
        # Step 2: Enter password (Subtask 9)
        logging.info("Writing password into 'パスワード' field.")
        helium.write(password, into=helium.TextField("パスワード"))
        
        # Second login button (Subtask 9)
        second_login_button_selectors = [
            helium.Button("ログインする"),
            helium.S("#submitto"),
            helium.S('input[type="submit"][value="ログインする"].btn.btn-primary.btn-block'),
            helium.S('input[type="submit"][value="ログインする"]')
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
        
        # 2FA handling logic (Subtask 12)
        try:
            helium.wait_until(helium.Button("手入力").exists, timeout_secs=10) 
            logging.info("Successfully logged in (found '手入力' button quickly).")
            logging.info(f"Navigating to MF_URL ({MF_URL}) after successful login.")
            helium.go_to(MF_URL)
            return 
        except Exception: 
            logging.info("'手入力' button not found with short timeout. Checking for 2FA page.")
            if is_2fa_page_detected(): 
                logging.info("2FA page detected. Attempting to handle 2FA.")
                handle_2fa_authentication() 
                logging.info("2FA handling complete. Waiting for '手入力' button again with longer timeout.")
                helium.wait_until(helium.Button("手入力").exists, timeout_secs=30) 
                logging.info("Successfully logged in after 2FA handling.")
                logging.info(f"Navigating to MF_URL ({MF_URL}) after successful login with 2FA.")
                helium.go_to(MF_URL)
                return 
            else:
                logging.info("2FA page not detected. Waiting for '手入力' with longer timeout or failing.")
                helium.wait_until(helium.Button("手入力").exists, timeout_secs=30) 
                logging.info("Successfully logged in (found '手入力' button after longer wait, no 2FA detected).")
                logging.info(f"Navigating to MF_URL ({MF_URL}) after successful login (no 2FA).")
                helium.go_to(MF_URL)
                return

    except Exception as e:
        logging.error(f"An error occurred during login: {e}")
        logging.info("Browser was running or an error occurred during login, attempting to close it.")
        helium.kill_browser() 
        raise 


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
