"""
カメラ転売リサーチボット - メインスクリプト
===============================================
動作フロー:
  1. カメラのキタムラ売れ筋から上位N件取得
  2. 各商品の同型品を安い順で取得
  3. メルカリで同型番の売り切れ価格を取得
  4. 利益が出る組み合わせをWindows通知

設定: config.json を編集してください
実行: python main.py              (一回実行)
      python main.py --schedule   (定期実行)
      python main.py --test       (テストモード)
"""
import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

import schedule
from playwright.sync_api import sync_playwright

from kitamura_scraper import KitamuraScraper, KitamuraProduct
from mercari_scraper import MercariScraper
from profit_calculator import ProfitCalculator
from notifier import notify_opportunity, notify_run_complete, notify_error

# ─────────── ロギング設定 ───────────
def setup_logging(log_file: str):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ─────────── 設定読み込み ───────────
def load_config(config_path: str = "config.json") -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


# ─────────── 結果保存 ───────────
def save_results(results: list[dict], output_file: str):
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if Path(output_file).exists():
        with open(output_file, encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except Exception:
                existing = []

    # 重複除去（同じkitamura_urlは上書き）
    existing_urls = {r["kitamura_url"] for r in existing}
    new_results = [r for r in results if r["kitamura_url"] not in existing_urls]
    combined = existing + new_results

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    logging.getLogger(__name__).info(f"結果保存: {output_file} ({len(combined)}件)")


# ─────────── メイン処理 ───────────
def run_once(config: dict, test_mode: bool = False) -> list[dict]:
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info(f"スキャン開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    kitamura_scraper = KitamuraScraper(config)
    mercari_scraper = MercariScraper(config)
    calculator = ProfitCalculator(config)

    profitable_results = []
    total_checked = 0

    try:
        with sync_playwright() as playwright:
            headless = config["settings"]["headless"]
            # Bot検知回避のためのブラウザ起動設定
            browser = playwright.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                    "--lang=ja-JP",
                ]
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                extra_http_headers={
                    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            # navigator.webdriver を隠す（最重要のBot検知対策）
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en-US', 'en'] });
                window.chrome = { runtime: {} };
            """)
            kitamura_page = context.new_page()
            mercari_page = context.new_page()

            # ① キタムラ売れ筋取得
            ranking_products = kitamura_scraper.get_ranking_products(kitamura_page)
            logger.info(f"売れ筋カメラ取得: {len(ranking_products)}件")

            if test_mode and ranking_products:
                # テストモード: 最初の2件だけ処理
                ranking_products = ranking_products[:2]
                logger.info("【テストモード】2件のみ処理します")

            for i, product in enumerate(ranking_products, 1):
                logger.info(f"\n--- [{i}/{len(ranking_products)}] {product.name[:40]} ---")

                # ② 詳細ページから型番・JANコード取得
                product = kitamura_scraper.get_product_detail(kitamura_page, product)

                # ③ 同型商品を安い順で取得
                same_type_products = kitamura_scraper.get_same_type_products(kitamura_page, product)

                # 同型がなければ元の商品を対象にする
                targets = same_type_products if same_type_products else [product]

                # 各商品でメルカリ比較
                query = product.model_number or product.jan_code or product.name
                mercari_stats = mercari_scraper.get_average_sold_price(
                    mercari_page, query, max_items=20
                )

                for target in targets[:5]:  # 安い順上位5件をチェック
                    total_checked += 1
                    result = calculator.calculate(target, mercari_stats, query)

                    if result and result.is_profitable:
                        profitable_results.append(result.to_dict())
                        # 通知
                        notify_opportunity(config, result)
                        time.sleep(1)  # 連続通知を少し間隔あける

                time.sleep(config["settings"]["request_delay_seconds"])

            browser.close()

    except Exception as e:
        logger.error(f"実行エラー: {e}")
        logger.error(traceback.format_exc())
        notify_error(config, str(e)[:150])

    # 結果サマリー
    logger.info("\n" + "=" * 60)
    logger.info(f"スキャン完了: チェック {total_checked}件, 利益あり {len(profitable_results)}件")
    logger.info("=" * 60)

    if profitable_results:
        save_results(profitable_results, config["results_file"])
        logger.info("\n【利益あり商品一覧】")
        for r in profitable_results:
            logger.info(
                f"  {r['model']} | 買:{r['buy_price']:,} → 推定:{r['estimated_sell']:,} "
                f"| 利益:¥{r['net_profit']:,} ({r['profit_rate_pct']}%)"
            )

    notify_run_complete(config, len(profitable_results), total_checked)
    return profitable_results


# ─────────── スケジュール実行 ───────────
def run_scheduled(config: dict):
    logger = logging.getLogger(__name__)
    interval_hours = config["settings"]["interval_hours"]
    logger.info(f"定期実行モード: {interval_hours}時間ごと")

    # 起動直後も1回実行
    run_once(config)

    schedule.every(interval_hours).hours.do(run_once, config=config)

    while True:
        schedule.run_pending()
        next_run = schedule.next_run()
        logger.info(f"次回実行: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(60)  # 1分ごとにチェック


# ─────────── エントリポイント ───────────
def main():
    parser = argparse.ArgumentParser(description="カメラ転売リサーチボット")
    parser.add_argument("--schedule", action="store_true", help="定期実行モード（6時間ごと）")
    parser.add_argument("--test", action="store_true", help="テストモード（2件のみ処理）")
    parser.add_argument("--config", default="config.json", help="設定ファイルパス")
    parser.add_argument("--no-headless", action="store_true", help="ブラウザを表示して実行")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.no_headless:
        config["settings"]["headless"] = False

    setup_logging(config["log_file"])

    if args.schedule:
        run_scheduled(config)
    else:
        run_once(config, test_mode=args.test)


if __name__ == "__main__":
    main()
