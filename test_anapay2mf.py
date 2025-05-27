import unittest
import base64
from datetime import datetime
from anapay2mf import ANAPay, get_mail_info, get_debit_card_mail_info
import anapay2mf  # For setting global variable

# Set up the global variable for testing
anapay2mf.debit_card_asset_name = "テスト用デビットカード資産名"

class TestEmailParsers(unittest.TestCase):
    def test_parse_debit_card_email_normal(self):
        body_text = """（前略）
【取引情報】
承認番号   ： 530567
利用日時   ： 2025/05/26 20:11:16
利用加盟店 ： GOOGLE *BNEI
引落通貨   ： JPY
引落金額   ： 4,754.00
（後略）"""
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_debit_res = {
            "payload": {
                "headers": [
                    {"name": "Date", "value": "Mon, 26 May 2025 20:30:00 +0900 (JST)"}
                ],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_debit_res)
        self.assertIsNotNone(result)
        self.assertEqual(result.email_date, datetime(2025, 5, 26, 20, 30, 0))
        self.assertEqual(result.date_of_use, datetime(2025, 5, 26, 20, 11, 16))
        self.assertEqual(result.amount, 4754)
        self.assertEqual(result.store, "GOOGLE *BNEI")
        self.assertEqual(result.transaction_type, "DebitCard")
        self.assertEqual(result.asset_name, "テスト用デビットカード資産名")

    def test_parse_debit_card_email_ana_pay_charge(self):
        body_text = """（前略）
【取引情報】
承認番号   ： 123456
利用日時   ： 2025/05/26 08:08:27
利用加盟店 ： ANA PAY
引落通貨   ： JPY
引落金額   ： 10,000.00
（後略）"""
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_debit_res = {
            "payload": {
                "headers": [
                    {"name": "Date", "value": "Mon, 26 May 2025 08:30:00 +0900 (JST)"}
                ],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_debit_res)
        self.assertIsNotNone(result)
        self.assertEqual(result.email_date, datetime(2025, 5, 26, 8, 30, 0))
        self.assertEqual(result.date_of_use, datetime(2025, 5, 26, 8, 8, 27))
        self.assertEqual(result.amount, 10000)
        self.assertEqual(result.store, "ANA PAY")
        self.assertEqual(result.transaction_type, "DebitCard")
        self.assertEqual(result.asset_name, "テスト用デビットカード資産名")

    def test_parse_debit_card_email_missing_info(self):
        body_text = """（前略）
【取引情報】
承認番号   ： 789012
利用日時   ： 2025/05/26 10:00:00
利用加盟店 ： SOME STORE
引落通貨   ： JPY
（引落金額が欠落）
（後略）"""
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_debit_res = {
            "payload": {
                "headers": [
                    {"name": "Date", "value": "Mon, 26 May 2025 10:05:00 +0900 (JST)"}
                ],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_debit_res)
        self.assertIsNone(result)

    def test_parse_anapay_email_normal(self):
        body_text = """（前略）
ご利用日時：2023-06-28 22:46:19
ご利用金額：44,308円
ご利用店舗：SMOKEBEERFACTORY OTSUKATE
（後略）"""
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_anapay_res = {
            "payload": {
                "headers": [
                    {"name": "Date", "value": "Wed, 28 Jun 2023 22:50:00 +0900 (JST)"}
                ],
                "body": {"data": encoded_body}
            }
        }
        result = get_mail_info(mock_anapay_res)
        self.assertIsNotNone(result)
        self.assertEqual(result.email_date, datetime(2023, 6, 28, 22, 50, 0))
        self.assertEqual(result.date_of_use, datetime(2023, 6, 28, 22, 46, 19))
        self.assertEqual(result.amount, 44308)
        self.assertEqual(result.store, "SMOKEBEERFACTORY OTSUKATE")
        self.assertEqual(result.transaction_type, "ANAPay")
        self.assertEqual(result.asset_name, "ANA Pay")

    def test_get_debit_card_mail_info_new_format_strict_spaces(self):
        """テスト: get_debit_card_mail_info 新フォーマット (TODO記載の厳密なスペース)"""
        body_text = """（前略）
利用日時　：　2024/03/15 10:30:00
利用加盟店　：　テストストア厳密
引落金額　：　1,234.00
（後略）"""
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_res = {
            "payload": {
                "headers": [{"name": "Date", "value": "Fri, 15 Mar 2024 10:35:00 +0900 (JST)"}],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_res)
        self.assertIsNotNone(result)
        self.assertEqual(result.email_date, datetime(2024, 3, 15, 10, 35, 0))
        self.assertEqual(result.date_of_use, datetime(2024, 3, 15, 10, 30, 0))
        self.assertEqual(result.amount, 1234)
        self.assertEqual(result.store, "テストストア厳密")
        self.assertEqual(result.transaction_type, "DebitCard")
        self.assertEqual(result.asset_name, anapay2mf.debit_card_asset_name) # グローバル変数を使用

    def test_get_debit_card_mail_info_new_format_minimal_spaces(self):
        """テスト: get_debit_card_mail_info 新フォーマット (最小限のスペース)"""
        body_text = """（前略）
利用日時：2024/03/15 11:00:00
利用加盟店：テストストア最小スペース
引落金額：500.00
（後略）"""
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_res = {
            "payload": {
                "headers": [{"name": "Date", "value": "Fri, 15 Mar 2024 11:05:00 +0900 (JST)"}],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_res)
        self.assertIsNotNone(result)
        self.assertEqual(result.date_of_use, datetime(2024, 3, 15, 11, 0, 0))
        self.assertEqual(result.amount, 500)
        self.assertEqual(result.store, "テストストア最小スペース")

    def test_get_debit_card_mail_info_invalid_date_format(self):
        """テスト: get_debit_card_mail_info 不正な日付フォーマット"""
        body_text = "利用日時　：　2024-03-15 10:30:00\n利用加盟店　：　テストストア\n引落金額　：　1,234.00"
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_res = {
            "payload": {
                "headers": [{"name": "Date", "value": "Fri, 15 Mar 2024 10:35:00 +0900 (JST)"}],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_res)
        self.assertIsNone(result)

    def test_get_debit_card_mail_info_invalid_amount_format(self):
        """テスト: get_debit_card_mail_info 不正な金額フォーマット"""
        body_text = "利用日時　：　2024/03/15 10:30:00\n利用加盟店　：　テストストア\n引落金額　：　ABC.00"
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_res = {
            "payload": {
                "headers": [{"name": "Date", "value": "Fri, 15 Mar 2024 10:35:00 +0900 (JST)"}],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_res)
        self.assertIsNone(result)

    def test_get_debit_card_mail_info_missing_store(self):
        """テスト: get_debit_card_mail_info 店舗名欠損"""
        body_text = "利用日時　：　2024/03/15 10:30:00\n引落金額　：　1,234.00"
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_res = {
            "payload": {
                "headers": [{"name": "Date", "value": "Fri, 15 Mar 2024 10:35:00 +0900 (JST)"}],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_res)
        self.assertIsNone(result)
    
    def test_get_debit_card_mail_info_amount_zero(self):
        """テスト: get_debit_card_mail_info 金額が0"""
        body_text = """利用日時　：　2024/03/15 10:30:00
利用加盟店　：　テストストアゼロ
引落金額　：　0.00"""
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_res = {
            "payload": {
                "headers": [{"name": "Date", "value": "Fri, 15 Mar 2024 10:35:00 +0900 (JST)"}],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_res)
        # Current implementation returns None if amount is not > 0
        self.assertIsNone(result) 

    def test_get_debit_card_mail_info_extra_lines_and_info(self):
        """テスト: get_debit_card_mail_info 余分な行や情報があっても抽出できるか"""
        body_text = """
これはテストメールです。
-------------------------
【取引情報】
承認番号   ： 987654
利用日時   ： 2025/12/24 18:00:00
これは途中の行です。
利用加盟店 ： クリスマスストア
引落通貨   ： JPY
引落金額   ： 5,000.00
備考       ： プレゼント代
-------------------------
ありがとうございました。
"""
        encoded_body = base64.urlsafe_b64encode(body_text.encode('utf-8')).decode('ascii')
        mock_res = {
            "payload": {
                "headers": [{"name": "Date", "value": "Tue, 24 Dec 2025 18:05:00 +0900 (JST)"}],
                "body": {"data": encoded_body}
            }
        }
        result = get_debit_card_mail_info(mock_res)
        self.assertIsNotNone(result)
        self.assertEqual(result.email_date, datetime(2025, 12, 24, 18, 5, 0))
        self.assertEqual(result.date_of_use, datetime(2025, 12, 24, 18, 0, 0))
        self.assertEqual(result.amount, 5000)
        self.assertEqual(result.store, "クリスマスストア")
        self.assertEqual(result.transaction_type, "DebitCard")


if __name__ == '__main__':
    unittest.main()
