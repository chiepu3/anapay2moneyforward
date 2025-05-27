# Handover Notes for anapay2moneyforward Project

## Project Objective
Automate the process of recording ANA Pay and Sumishin SBI Net Bank debit card usage history from Gmail notifications to Money Forward ME.

## Session Summary
This session focused on adapting the script for Money Forward ME login changes and implementing 2FA handling. Significant progress was made, but some tasks remain incomplete due to persistent Git environment issues.

## Completed Changes & Current Status

### 1. Money Forward ME Login Adaptation
*   **Login URL:** `MF_URL` in `anapay2mf.py` updated to `https://moneyforward.com/cf`.
*   **Two-Step Login:** `login_mf` function modified to handle the email-first, then password login flow of `moneyforward.com/users/sign_in`.
    *   Uses visible text (e.g., "メールアドレス", "パスワード") for field identification.
    *   Robust selectors for login buttons.
*   **"Keep me logged in":** Logic added to attempt clicking the "次回から自動的にログインする" checkbox. `CheckBox` selector fixed to use visible text.
*   **Error Handling:** Enhanced within `login_mf`.

### 2. Configuration
*   **`SHEET_ID` as Environment Variable:** Removed hardcoded `SHEET_ID` from `anapay2mf.py`. It's now loaded via `os.getenv("SHEET_ID")`.
*   **`README.md` Update:** Instructions updated to guide users to set `SHEET_ID` in their `.env` file.

### 3. Two-Factor Authentication (2FA) Handling Framework
*   **2FA Page Detection (`is_2fa_page_detected` function):**
    *   Implemented to detect the Money Forward 2FA page using HTML elements:
        *   Heading: `<h1>追加認証のお願い</h1>` (checked via `helium.S("//h1[contains(text(),'追加認証のお願い')]").exists()`)
        *   OTP Input Field: `id="email_otp"` (checked via `helium.S("input#email_otp").exists()`)
*   **Gmail 2FA Code Fetching (`fetch_latest_mf_2fa_code_from_gmail` function):**
    *   Fully implemented to:
        *   Authenticate with Gmail API (including token refresh via `quickstart.main` if needed).
        *   Search for emails from `do_not_reply@moneyforward.com` with subject `マネーフォワード ID メールによる追加認証` received in the last 10 minutes.
        *   Parse email body using regex `r"こちらのコードを入力してログインを継続してください。\s*(\d{6})"` to extract the 6-digit code.
*   **2FA Orchestration (`handle_2fa_authentication` function):**
    *   Calls `fetch_latest_mf_2fa_code_from_gmail`.
    *   If code is retrieved, it's intended to call `submit_2fa_code_to_page` (currently a placeholder).
    *   Raises an exception if code retrieval fails.
*   **`login_mf` Integration:**
    *   Modified to attempt login with a short timeout.
    *   If initial login doesn't find "手入力" button, it calls `is_2fa_page_detected`.
    *   If 2FA page is detected, calls `handle_2fa_authentication`.
    *   Retries waiting for "手入力" button after 2FA attempt or if 2FA was not detected initially.

## Pending Tasks

### 1. Implement 2FA Code Submission (`submit_2fa_code_to_page` function)
*   **Objective:** Submit the 6-digit 2FA code (fetched by `fetch_latest_mf_2fa_code_from_gmail`) to the Money Forward 2FA page using Helium.
*   **Function Signature:** `def submit_2fa_code_to_page(auth_code: str):`
*   **Target Page HTML Elements (User-provided):**
    *   **Input Field:** `<input ... id="email_otp" ... placeholder="000000" ...>`
        *   Recommended Helium selectors: `helium.S("#email_otp")` (primary), `helium.TextField("000000")` (fallback).
    *   **Submit Button:** `<button id="submitto" ...>認証する</button>`
        *   Recommended Helium selectors: `helium.Button("認証する")` (primary), `helium.S("#submitto")` (fallback).
*   **Implementation Notes:**
    *   The function should use `helium.write()` for the input field and `helium.click()` for the button.
    *   Include robust error handling for element not found / interaction failures.
    *   This function is called from `handle_2fa_authentication`.
*   **Last Attempted Code (Conceptual - verify before use):**
    ```python
    # import helium
    # import logging
    # def submit_2fa_code_to_page(auth_code: str):
    #     logging.info(f"Attempting to submit 2FA code '{auth_code}' to the page.")
    #     otp_input_field_selectors = [helium.S("#email_otp"), helium.TextField("000000")]
    #     submit_button_selectors = [helium.Button("認証する"), helium.S("#submitto")]
    #     input_field_found_and_written = False
    #     for selector in otp_input_field_selectors:
    #         if selector.exists():
    #             logging.info(f"Found 2FA input field with selector: {selector}")
    #             try:
    #                 if isinstance(selector, helium.S) and helium.get_driver().find_element(*selector.internal_selector_value).tag_name == 'input':
    #                     helium.click(selector) 
    #                 helium.write(auth_code, into=selector)
    #                 input_field_found_and_written = True; break
    #             except Exception as e_write: logging.warning(f"Error writing with {selector}: {e_write}")
    #     if not input_field_found_and_written: raise Exception("2FA input field not found/writable.")
    #     submit_button_found_and_clicked = False
    #     for selector in submit_button_selectors:
    #         if selector.exists():
    #             logging.info(f"Found 2FA submit button with selector: {selector}")
    #             try: helium.click(selector); submit_button_found_and_clicked = True; break
    #             except Exception as e_click: logging.warning(f"Error clicking with {selector}: {e_click}")
    #     if not submit_button_found_and_clicked: raise Exception("2FA submit button not found/clickable.")
    #     logging.info("2FA code submitted successfully.")
    ```

### 2. Correct Debit Card Email Parsing (`get_debit_card_mail_info` function)
*   **Objective:** Update `get_debit_card_mail_info` in `anapay2mf.py` for Sumishin SBI Net Bank debit card emails.
*   **Correct Email Specifications (User-provided):**
    *   **Subject:** `【デビットカード】ご利用のお知らせ(住信SBIネット銀行)`
    *   **Body Parsing - Key Info & Patterns:**
        *   Date/Time: `利用日時   ： YYYY/MM/DD HH:MM:SS`
        *   Merchant: `利用加盟店 ： ACTUAL STORE NAME`
        *   Amount: `引落金額   ： N,NNN.NN` (e.g., `4,754.00`)
*   **Actions:**
    *   Update Gmail search query in `get_transaction_emails` for debit card subject. (Current sender `from:post_master@netbk.co.jp` is assumed okay).
    *   Modify line parsing in `get_debit_card_mail_info` for new labels and amount format (handle commas, convert to int/float).

## Persistent Environment Issues
*   **Git Index Lock:** Frequent errors `fatal: Unable to create '/app/.git/index.lock': File exists.`
*   **Diff Application Failures:** `Invalid merge diff: diff did not apply. The following search blocks were not found in the file.`
*   These issues prevented reliable code changes using `replace_with_git_merge_diff`, even after `reset_all()` attempts.

## Recommended Next Steps for Next Agent
1.  **Stabilize Environment:** Ensure the Git repository in the execution environment is stable and responsive. This might require intervention from platform support if `reset_all()` is insufficient.
2.  **Implement `submit_2fa_code_to_page`:** Use the details above.
3.  **Implement Debit Card Email Fix:** Use the details above.
4.  **Thorough Testing:** Test the full login flow (including 2FA) and transaction posting for both ANA Pay and debit cards.
5.  **Commit Changes:** Commit the new implementations and any necessary fixes.

## Key Files
*   `anapay2mf.py`: Main script.
*   `README.md`: Setup and usage instructions.
*   `TODO_remaining_tasks.md`: Contains a more concise version of these pending tasks.
*   `.env` (user-managed): For credentials and `SHEET_ID`.
