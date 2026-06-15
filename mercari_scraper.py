"""
メルカリ スクレイパー
売り切れ商品の価格を取得（Playwright使用）

メルカリのHTML構造（2026年4月確認）:
 - 商品カード: li[data-testid="item-cell"]
 - サムネイル: mer-item-thumbnail[aria-label]
 - aria-label形式: "商品名の画像 売り切れ 210,000円"
 - 価格・売り切れ・商品名はすべてaria-labelから取得可能
"""
import time
import logging
import re
import statistics
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


@dataclass
class MercariSoldItem:
    title: str
    price: int


class MercariScraper:
    SEARCH_BASE_URL = "https://jp.mercari.com/search"

    def __init__(self, config: dict):
        self.delay = config["settings"]["request_delay_seconds"]

    def search_sold_items(
        self, page, query: str, max_items: int = 20
    ) -> list[MercariSoldItem]:
        """
        指定クエリで売り切れ商品を検索して価格リストを返す
        """
        url = (
            f"{self.SEARCH_BASE_URL}"
            f"?keyword={quote(query, safe='')}"
            f"&status=sold_out"
            f"&sort=created_time"
            f"&order=desc"
        )
        logger.info(f"  メルカリ検索: '{query}' → {url}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=40000)
            # JavaScriptの描画を十分待つ (GitHub Actionsは遅め)
            page.wait_for_timeout(10000)
        except Exception as e:
            logger.warning(f"  メルカリ読み込みエラー: {e}")

        # デバッグ用スクリーンショット
        try:
            page.screenshot(path="debug_mercari_actions.png", full_page=False)
        except: pass

        # 商品項目を特定
        items_locator = page.locator('li[data-testid="item-cell"]')

        sold_items = []
        try:
            count = items_locator.count()
            logger.info(f"  メルカリ項目数検出: {count}")

            for i in range(min(count, max_items)):
                item = items_locator.nth(i)
                
                # aria-label を持つ要素（サムネイルなど）を探す
                # ここの aria-label が最も正確な情報を持っていることが多い
                label = ""
                thumbnail = item.locator('[aria-label]').first
                if thumbnail.count() > 0:
                    label = thumbnail.get_attribute('aria-label') or ""
                
                text = item.inner_text()
                
                # 解析対象の文字列 (labelとtextの両方をチェック)
                combined_source = (label + " " + text).replace('\n', ' ')
                
                # 「売り切れ」確認
                if not ("売り切れ" in combined_source or "SOLD" in combined_source):
                    continue
                
                # 価格の抽出 (¥12,345 or 12,345円)
                price = 0
                price_match = re.search(r'([¥￥][\d,]+)', combined_source) or re.search(r'([\d,]+)円', combined_source)
                
                if price_match:
                    price_str = price_match.group(1).replace('¥', '').replace('￥', '').replace(',', '').replace('円', '')
                    try:
                        price = int(price_str)
                    except:
                        pass
                
                if price > 0:
                    # 商品名の取得 (aria-labelから商品名を抜くのが正確)
                    title = "不明"
                    title_match = re.search(r'^(.+?)(?:の画像|$)', label)
                    if title_match:
                        title = title_match.group(1).strip()
                    else:
                        title = text.split('\n')[0].strip()
                    
                    if 1000 < price < 5000000:
                        sold_items.append(MercariSoldItem(title=title, price=price))
                
        except Exception as e:
            logger.warning(f"  メルカリ解析エラー: {e}")


        # もしlocatorで取得できなかった場合の最終手段 (ブラウザ全体のテキストから抽出)
        if not sold_items:
            logger.info("  ロケーターで取得不可、全体テキストから抽出を試みます")
            full_text = page.inner_text('body')
            # "¥ 123,456 売り切れ" のようなパターンを探す
            # 複雑な正規表現で売り切れ近辺の価格を拾う
            matches = re.findall(r'([¥￥][\d,]+)\s*(?:売り切れ|SOLD)', full_text)
            for m in matches[:max_items]:
                price = int(m.replace('¥', '').replace('￥', '').replace(',', '').strip())
                if 1000 < price < 5000000:
                    sold_items.append(MercariSoldItem(title="不明", price=price))

        logger.info(f"  メルカリ最終取得: {len(sold_items)}件")
        return sold_items

    def get_average_sold_price(
        self, page, query: str, max_items: int = 20
    ) -> Optional[dict]:
        """
        売り切れ価格の統計を返す
        """
        time.sleep(self.delay)
        sold = self.search_sold_items(page, query, max_items)

        if not sold:
            return None

        prices = [s.price for s in sold]

        # 外れ値除去（IQR法）
        prices = _remove_outliers(prices)
        if len(prices) < 2:
            # サンプルが少なすぎる場合は外れ値除去なしで試行
            if len(sold) >= 1:
                prices = [s.price for s in sold]
            else:
                return None

        return {
            "avg": int(statistics.mean(prices)),
            "median": int(statistics.median(prices)),
            "min": min(prices),
            "max": max(prices),
            "count": len(prices),
            "items": sold,
        }



def _remove_outliers(prices: list[int]) -> list[int]:
    """IQR法で外れ値を除去"""
    if len(prices) < 4:
        return prices
    sorted_p = sorted(prices)
    q1 = sorted_p[len(sorted_p) // 4]
    q3 = sorted_p[3 * len(sorted_p) // 4]
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    filtered = [p for p in prices if lower <= p <= upper]
    return filtered if filtered else prices
