"""メルカリの商品カード構造を詳しく調査"""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    page.goto("https://jp.mercari.com/search?keyword=EOS+R6+MarkII&status=sold_out", wait_until="domcontentloaded")
    page.wait_for_timeout(8000)

    result = page.evaluate("""
        () => {
            // 全セレクタ試し
            const selectors = {
                'ul li': document.querySelectorAll('ul li').length,
                '[role="listitem"]': document.querySelectorAll('[role="listitem"]').length,
                'figure': document.querySelectorAll('figure').length,
                'img[alt]': document.querySelectorAll('img[alt]').length,
                '[data-location]': document.querySelectorAll('[data-location]').length,
            };
            
            // 価格含む要素探し
            const allText = document.body.innerText;
            const priceCount = (allText.match(/[\\d,]+円/g) || []).length;
            
            // 250万以下で1000以上の価格っぽい数字
            const priceTexts = (allText.match(/[\\d,]+円/g) || []).slice(0, 10);
            
            // ul li のサンプル
            const liSamples = [];
            document.querySelectorAll('ul li').forEach(li => {
                const t = li.innerText.trim();
                if (t.includes('円') && t.length > 5) {
                    liSamples.push(t.substring(0, 100));
                }
            });
            
            return {
                selectors,
                priceCount,
                priceTexts,
                liSamplesWithPrice: liSamples.slice(0, 3),
                // 全HTMLの最初の3000文字
                htmlSample: document.body.innerHTML.substring(5000, 7000)
            };
        }
    """)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    browser.close()
