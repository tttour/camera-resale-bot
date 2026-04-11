"""
利益計算モジュール
キタムラ購入価格 vs メルカリ売却想定価格 を比較して利益判定
"""
import logging
from dataclasses import dataclass
from typing import Optional
from kitamura_scraper import KitamuraProduct

logger = logging.getLogger(__name__)

# メルカリ手数料率（2024年現在）
MERCARI_FEE_RATE = 0.10  # 10%
# 送料目安（カメラクラスは宅急便60サイズ程度）
SHIPPING_COST_MIN = 800
SHIPPING_COST_MAX = 1500
SHIPPING_COST_DEFAULT = 1000


@dataclass
class ProfitResult:
    kitamura_product: KitamuraProduct
    buy_price: int                   # キタムラ購入価格
    mercari_median_price: int        # メルカリ中央値
    mercari_avg_price: int           # メルカリ平均
    mercari_sample_count: int        # サンプル数
    estimated_sell_price: int        # 想定売却価格（中央値の90%）
    mercari_fee: int                 # メルカリ手数料
    shipping_cost: int               # 送料
    net_profit: int                  # 純利益
    profit_rate: float               # 利益率
    is_profitable: bool
    mercari_url: str = ""            # メルカリ検索URL

    def to_dict(self) -> dict:
        return {
            "model": self.kitamura_product.model_number or self.kitamura_product.name,
            "condition": self.kitamura_product.condition,
            "store": self.kitamura_product.store_name,
            "buy_price": self.buy_price,
            "mercari_median": self.mercari_median_price,
            "mercari_avg": self.mercari_avg_price,
            "mercari_samples": self.mercari_sample_count,
            "estimated_sell": self.estimated_sell_price,
            "mercari_fee": self.mercari_fee,
            "shipping": self.shipping_cost,
            "net_profit": self.net_profit,
            "profit_rate_pct": round(self.profit_rate * 100, 1),
            "is_profitable": self.is_profitable,
            "kitamura_url": self.kitamura_product.product_url,
            "mercari_url": self.mercari_url,
        }

    def format_message(self) -> str:
        emoji = "🎯" if self.net_profit >= 5000 else "✅"
        model = self.kitamura_product.model_number or self.kitamura_product.name[:30]
        return (
            f"{emoji} 【利益あり】{model} ({self.kitamura_product.condition})\n"
            f"  購入価格: ¥{self.buy_price:,}\n"
            f"  メルカリ中央値: ¥{self.mercari_median_price:,} (n={self.mercari_sample_count})\n"
            f"  想定売却価格: ¥{self.estimated_sell_price:,}\n"
            f"  ✨ 純利益: ¥{self.net_profit:,} ({self.profit_rate*100:.1f}%)\n"
            f"  📍 店舗: {self.kitamura_product.store_name}\n"
            f"  🛒 キタムラ: {self.kitamura_product.product_url}\n"
            f"  📦 メルカリ: {self.mercari_url}"

        )


class ProfitCalculator:
    def __init__(self, config: dict):
        self.min_profit = config["settings"]["min_profit_yen"]
        self.mercari_fee_rate = config["settings"].get("mercari_fee_rate", MERCARI_FEE_RATE)
        self.shipping_cost = SHIPPING_COST_DEFAULT

    def calculate(
        self,
        kitamura_product: KitamuraProduct,
        mercari_stats: Optional[dict],
        mercari_query: str = "",
    ) -> Optional[ProfitResult]:
        """
        利益計算を実行
        mercari_stats: mercari_scraper.MercariScraper.get_average_sold_price の返り値
        """
        if not mercari_stats or mercari_stats["count"] < 3:
            logger.debug(f"  サンプル不足 (n={mercari_stats['count'] if mercari_stats else 0})")
            return None

        buy_price = kitamura_product.price

        # 中央値の90%を安全な売却想定価格として使用（実際には値引き交渉考慮）
        median = mercari_stats["median"]
        estimated_sell = int(median * 0.90)

        # コスト計算
        mercari_fee = int(estimated_sell * self.mercari_fee_rate)
        total_cost = buy_price + self.shipping_cost
        total_deductions = mercari_fee + self.shipping_cost
        net_profit = estimated_sell - total_cost - mercari_fee

        profit_rate = net_profit / buy_price if buy_price > 0 else 0

        # メルカリ検索URL生成
        from urllib.parse import quote
        mercari_url = f"https://jp.mercari.com/search?keyword={quote(mercari_query, safe='')}&status=sold_out"

        is_profitable = net_profit >= self.min_profit

        result = ProfitResult(
            kitamura_product=kitamura_product,
            buy_price=buy_price,
            mercari_median_price=median,
            mercari_avg_price=mercari_stats["avg"],
            mercari_sample_count=mercari_stats["count"],
            estimated_sell_price=estimated_sell,
            mercari_fee=mercari_fee,
            shipping_cost=self.shipping_cost,
            net_profit=net_profit,
            profit_rate=profit_rate,
            is_profitable=is_profitable,
            mercari_url=mercari_url,
        )

        if is_profitable:
            logger.info(f"  [PROFIT] {result.format_message()}")
        else:
            logger.info(
                f"  [NO PROFIT] {kitamura_product.name[:30]} "
                f"Buy:{buy_price:,} -> Est:{estimated_sell:,} Profit:{net_profit:,}"
            )


        return result
