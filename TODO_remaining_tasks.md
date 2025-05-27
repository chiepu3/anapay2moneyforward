# TODO: Remaining Tasks

This file outlines tasks that were pending due to environmental issues or prioritization.

## 1. Implement 2FA Code Submission (`submit_2fa_code_to_page` function)

**Objective:**
Submit the 6-digit 2FA code (fetched from Gmail) to the Money Forward 2FA page using Helium.

**Target Page HTML Elements (User-provided):**
*   **Input Field for 2FA Code:**
    *   ID: `email_otp`
    *   HTML: `<input class="rR4ct5ug" required="" autocomplete="one-time-code" id="email_otp" inputmode="numeric" placeholder="000000" name="email_otp">`
    *   Helium selector examples: `helium.S("#email_otp")` or `helium.TextField("000000")`.
*   **Submit Button:**
    *   ID: `submitto`
    *   Text: "認証する"
    *   HTML: `<button id="submitto" class="NCvulA39">認証する</button>`
    *   Helium selector examples: `helium.Button("認証する")` or `helium.S("#submitto")`.

**Function Signature:**
`def submit_2fa_code_to_page(auth_code: str):`

**Implementation Notes:**
*   The function should use Helium to `write` the `auth_code` into the input field.
*   Then, it should `click` the submit button.
*   Include error handling in case the elements are not found.
*   This function is called from `handle_2fa_authentication` after the code is fetched by `fetch_latest_mf_2fa_code_from_gmail`.

## 2. Correct Debit Card Email Parsing (`get_debit_card_mail_info` function)

**Objective:**
Update the `get_debit_card_mail_info` function in `anapay2mf.py` to correctly parse details from Sumishin SBI Net Bank debit card notification emails, based on the user-provided format.

**Correct Email Specifications (User-provided):**
*   **Subject:** `【デビットカード】ご利用のお知らせ(住信SBIネット銀行)`
*   **Body Parsing Logic - Key Information & Patterns:**
    *   **Transaction Date/Time:**
        *   Label: `利用日時   ： ` (Label followed by two full-width spaces and a colon)
        *   Format: `YYYY/MM/DD HH:MM:SS`
        *   Example: `利用日時   ： 2025/05/26 20:11:16`
    *   **Merchant Name:**
        *   Label: `利用加盟店 ： ` (Label followed by two full-width spaces and a colon)
        *   Example: `利用加盟店 ： GOOGLE *BNEI`
    *   **Amount:**
        *   Label: `引落金額   ： ` (Label followed by two full-width spaces and a colon)
        *   Format: `N,NNN.NN` (No currency symbol, comma for thousands separator, two decimal places)
        *   Example: `引落金額   ： 4,754.00`

**Function to Modify:**
`get_debit_card_mail_info(res: dict) -> ANAPay | None:`

**Implementation Notes:**
*   Update the Gmail search query in `get_transaction_emails` for debit card emails to use the new subject line. The current query is:
    `"debit": f"from:post_master@netbk.co.jp subject:デビットカードのご利用がありました。 after:{after}"`
    It should be changed to:
    `"debit": f"from:post_master@netbk.co.jp subject:(【デビットカード】ご利用のお知らせ(住信SBIネット銀行)) after:{after}"` (Note: `post_master@netbk.co.jp` as sender is assumed correct unless user specifies otherwise for this email type).
*   Inside `get_debit_card_mail_info`, adjust the line-parsing logic to match the new labels and value formats.
*   Specifically, the amount parsing needs to handle commas and convert to an integer or float as appropriate for the `ANAPay` class (currently `amount: int = 0`). If it's always `.00`, then converting to `int(float(value.replace(",", "")))` is suitable.
