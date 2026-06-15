"""
カメラのキタムラ スクレイパー
売れ筋カメラの一覧取得 → 同型商品を安い順で取得
"""
import time
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)


@dataclass
class KitamuraProduct:
    name: str
    price: int
    condition: str = "不明"
    product_url: str = ""
    store_name: str = "不明"
    jan_code: Optional[str] = None
    model_number: Optional[str] = None
    same_type_url: Optional[str] = None  # 安い順でソート済みURL


class KitamuraScraper:
    RANKING_URL = "https://shop.kitamura.jp/ec/list?type=u&sort=recom:desc"
    BASE_URL = "https://shop.kitamura.jp"

    def __init__(self, config: dict):
        self.config = config
        self.delay = config["settings"].get("request_delay_seconds", 2)
        self.top_count = config["settings"].get("top_cameras_count", 10)
        self.max_pages = config["settings"].get("same_type_max_pages", 3)
        self.target_conditions = config["settings"].get("target_conditions", ["A", "AB"])
        self.target_store_keywords = config["settings"].get("target_store_keywords", [])

    def _goto_and_wait(self, page: Page, url: str):
        """ページ遷移してコンテンツが描画されるまで待つ"""
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        # Vue.js描画完了を「リンクが出現するまで待つ」方式で確実に検知
        try:
            page.wait_for_selector(
                'a[href*="/ec/used/"], a.product-link, main',
                timeout=25000
            )
        except Exception:
            # タイムアウトしても処理は続行
            page.wait_for_timeout(5000)

    def get_ranking_products(self, page: Page) -> list[KitamuraProduct]:
        """売れ筋ランキングから上位カメラを取得"""
        logger.info(f"ランキング取得開始: {self.RANKING_URL}")
        self._goto_and_wait(page, self.RANKING_URL)

        # 診断ログ: ページ取得状況を確認
        diag = page.evaluate("""
            () => ({
                title: document.title,
                productLinks: document.querySelectorAll('a.product-link').length,
                totalLinks: document.querySelectorAll('a').length,
                bodyLength: document.body.innerText.length
            })
        """)
        logger.info(f"ページ診断: タイトル='{diag['title']}', a.product-link数={diag['productLinks']}, 総リンク数={diag['totalLinks']}, テキスト長={diag['bodyLength']}")

        # スクリーンショット保存 (GitHub Actionsのartifactとして確認可能)
        try:
            page.screenshot(path="debug_kitamura_actions.png", full_page=False)
            logger.info("スクリーンショット保存: debug_kitamura_actions.png")
        except Exception as e:
            logger.warning(f"スクリーンショット失敗: {e}")

        products_data = page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // 方法1: a.product-link (従来のセレクタ)
                document.querySelectorAll('a.product-link').forEach(el => {
                    const href = el.getAttribute('href') || '';
                    if (!href.match(/\\/ec\\/used\\/\\d+/)) return;
                    if (seen.has(href)) return;
                    seen.add(href);

                    const text = el.innerText || '';
                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                    const priceMatch = text.match(/([\\d,]+)円/);
                    const condMatch = text.match(/\\b(AA|AB|BB|B|A|C|S)\\b/);

                    if (priceMatch && lines[0]) {
                        results.push({
                            name: lines[0],
                            price: parseInt(priceMatch[1].replace(/,/g, '')),
                            condition: condMatch ? condMatch[1] : '不明',
                            href: href,
                            store_name: '不明'
                        });
                    }
                });

                // 方法2: /ec/used/ を含む全てのaタグ (フォールバック)
                if (results.length === 0) {
                    document.querySelectorAll('a[href*="/ec/used/"]').forEach(el => {
                        const href = el.getAttribute('href') || '';
                        if (!href.match(/\\/ec\\/used\\/\\d+/)) return;
                        if (seen.has(href)) return;

                        // 親要素も含めてテキストを取得
                        const container = el.closest('li, .product-card, [class*="product"], [class*="item"]') || el;
                        const text = container.innerText || el.innerText || '';
                        const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                        const priceMatch = text.match(/([\\d,]+)円/);
                        const condMatch = text.match(/\\b(AA|AB|BB|B|A|C|S)\\b/);

                        if (priceMatch && lines[0] && lines[0].length > 5) {
                            seen.add(href);
                            results.push({
                                name: lines[0],
                                price: parseInt(priceMatch[1].replace(/,/g, '')),
                                condition: condMatch ? condMatch[1] : '不明',
                                href: href,
                                store_name: '不明'
                            });
                        }
                    });
                }

                return results;
            }
        """)

        products = []
        seen_urls = set()
        for d in products_data:
            url = self.BASE_URL + d["href"] if d["href"].startswith("/") else d["href"]
            if url in seen_urls: continue
            
            # ランキング段階では店舗不明を許可（詳細で確定させるため）
            if not self._is_target_condition(d["condition"]): continue
            if not self._is_target_store(d["store_name"], allow_unknown=True): continue

            seen_urls.add(url)
            products.append(KitamuraProduct(
                name=d["name"], price=d["price"], condition=d["condition"],
                product_url=url, store_name=d["store_name"]
            ))
            if len(products) >= self.top_count: break

        logger.info(f"ランキングから候補抽出: {len(products)}件")
        return products

    def get_product_detail(self, page: Page, product: KitamuraProduct) -> KitamuraProduct:
        """詳細ページからJAN・型番・店舗情報を取得"""
        logger.info(f"詳細解析中: {product.name[:30]}")
        try:
            self._goto_and_wait(page, product.product_url)
        except: return product

        detail = page.evaluate("""
            () => {
                const res = { jan: null, sameHref: null, store: '不明' };
                const html = document.body.innerHTML;
                const text = document.body.innerText;

                // 同型商品リンク (hrefにkeyword3が含まれるものを優先)
                const sameEl = document.querySelector('a.product-btn-area, a[href*="keyword3"]');
                if (sameEl) res.sameHref = sameEl.getAttribute('href');

                // JAN
                const janM = html.match(/keyword3=([\\d]+)/);
                if (janM) res.jan = janM[1];

                // 店舗名 (もっと広範囲に探す)
                const storeM = text.match(/取扱店[：:]\\s*([^\\n\\t]+(?:店|写真機店|センター))/);
                if (storeM) res.store = storeM[1].trim();

                return res;
            }
        """)

        if detail["jan"]: product.jan_code = detail["jan"]
        if detail["store"] != "不明": product.store_name = detail["store"]
        
        if detail["sameHref"]:
            u = detail["sameHref"]
            url = self.BASE_URL + u if u.startswith("/") else u
            product.same_type_url = self._add_sort_param(url)
        elif product.jan_code:
            product.same_type_url = f"{self.BASE_URL}/ec/list?keyword3={product.jan_code}&type=u&sort=price:asc"

        product.model_number = self._extract_model_number(product.name)
        logger.info(f"  結果: JAN={product.jan_code}, 店舗={product.store_name}, 同型URL={'あり' if product.same_type_url else 'なし'}")
        return product

    def get_same_type_products(self, page: Page, product: KitamuraProduct) -> list[KitamuraProduct]:
        """安い順リストからターゲット店舗の商品を抽出"""
        if not product.same_type_url: return []
        logger.info(f"  同型商品検索開始: {product.same_type_url}")
        
        all_results = []
        for page_num in range(self.max_pages):
            url = product.same_type_url
            if page_num > 0:
                url = self._add_or_update_param(url, "offset", str(page_num * 40 + 1))
            
            try:
                self._goto_and_wait(page, url)
            except: break

            items_data = page.evaluate("""
                () => {
                    const found = [];
                    document.querySelectorAll('a.product-link').forEach(el => {
                        const t = el.innerText;
                        const pM = t.match(/([\\d,]+)円/);
                        const cM = t.match(/\\b([A-S]{1,2})\\b/);
                        const sM = t.match(/([^\\n]+(?:店|写真機店|センター))/);
                        if (pM && el.getAttribute('href').includes('/ec/used/')) {
                            found.push({
                                href: el.getAttribute('href'),
                                price: parseInt(pM[1].replace(/,/g, '')),
                                condition: cM ? cM[1] : '不明',
                                store: sM ? sM[1].trim() : '不明'
                            });
                        }
                    });
                    return found;
                }
            """)

            if not items_data: break
            for d in items_data:
                if not self._is_target_condition(d["condition"]): continue
                if not self._is_target_store(d["store"]): continue

                all_results.append(KitamuraProduct(
                    name=product.name, price=d["price"], condition=d["condition"],
                    product_url=self.BASE_URL + d["href"], store_name=d["store"],
                    jan_code=product.jan_code, model_number=product.model_number
                ))
            if len(all_results) >= 10: break # 多すぎても処理が重いので10件まで
            time.sleep(self.delay)

        all_results.sort(key=lambda x: x.price)
        logger.info(f"  フィルタ後の同型商品: {len(all_results)}件")
        return all_results

    def _is_target_condition(self, cond: str) -> bool:
        return any(c in cond for c in self.target_conditions)

    def _is_target_store(self, store: str, allow_unknown: bool = False) -> bool:
        if allow_unknown and store == "不明": return True
        if not self.target_store_keywords: return True
        return any(kw in store for kw in self.target_store_keywords)

    def _add_sort_param(self, url: str) -> str:
        if "sort=" in url: return re.sub(r"sort=[^&]+", "sort=price:asc", url)
        return url + ("&" if "?" in url else "?") + "sort=price:asc"

    def _add_or_update_param(self, url: str, key: str, value: str) -> str:
        pattern = rf"{re.escape(key)}=[^&]+"
        if re.search(pattern, url): return re.sub(pattern, f"{key}={value}", url)
        return url + ("&" if "?" in url else "?") + f"{key}={value}"

    def _extract_model_number(self, name: str) -> Optional[str]:
        patterns = [r"(EOS\s*R\d+[A-Z\s]*(?:Mark\s*I+)?)", r"(EOS\s*[\d]+[A-Z]*)", r"(α\s*\d+[IVX]*[A-Z]?)", r"(Z\s*\d+[f]?(?:\s*II)?)"]
        for p in patterns:
            m = re.search(p, name, re.IGNORECASE)
            if m: return m.group(1).strip()
        return None
