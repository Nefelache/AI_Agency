"""
数据获取模块 — 全真实公开权威数据源，零模拟数据

数据来源清单：
  1. Open-Meteo Historical Weather API
     https://archive-api.open-meteo.com/v1/archive
     完全免费，无需 API Key，WMO 气象网络历史数据
  2. UN Comtrade Public API (新版 v1 + 旧版 legacy 双备份)
     https://comtradeapi.un.org/public/v1/preview/C/A/HS
     https://comtrade.un.org/api/get   (旧版备用)
     联合国官方商品贸易统计数据库
  3. 生意社 SunSirs (www.sunsirs.com) — 大宗商品现货价格
  4. 百川盈孚 (www.100ppi.com)          — 化工品现货价格（备用）
"""

import os
import re
import time
import json
from datetime import date, datetime

import requests
import pandas as pd
from bs4 import BeautifulSoup

# ─── 缓存文件路径 ────────────────────────────────────────────
TRADE_CACHE_FILE  = os.path.join(os.path.dirname(__file__), "real_trade_data.csv")
WEATHER_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".weather_cache")

# ─── 通用请求头（模拟真实浏览器，降低被拒概率）─────────────────
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# ══════════════════════════════════════════════════════════════════════════════
# 模块 1：Open-Meteo 历史气象数据
# ══════════════════════════════════════════════════════════════════════════════

def fetch_open_meteo_weather(
    latitude: float,
    longitude: float,
    location_name: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """
    从 Open-Meteo 历史气象 API 获取真实逐小时气象数据并聚合为月均值。

    官方文档：https://open-meteo.com/en/docs/historical-weather-api
    API 端点：https://archive-api.open-meteo.com/v1/archive
    许可证：Open-Meteo Data — CC BY 4.0（含 ERA5 再分析数据）

    参数
    ----
    latitude      纬度（十进制度）
    longitude     经度（十进制度）
    location_name 地点标签（用于图表图例）
    start_date    数据起始日期，格式 "YYYY-MM-DD"
    end_date      数据结束日期，格式 "YYYY-MM-DD"

    返回
    ----
    pd.DataFrame  列：year_month_dt | avg_humidity | total_precipitation | location
    """
    os.makedirs(WEATHER_CACHE_DIR, exist_ok=True)
    cache_key = f"{location_name}_{start_date}_{end_date}.parquet".replace(" ", "_").replace("（", "").replace("）", "")
    cache_path = os.path.join(WEATHER_CACHE_DIR, cache_key)

    # 使用本地缓存避免重复请求（气象历史数据不会变化）
    if os.path.exists(cache_path):
        try:
            df = pd.read_parquet(cache_path)
            return df
        except Exception:
            pass  # 缓存损坏则重新拉取

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude":  latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date":   end_date,
        # relative_humidity_2m: 地面 2 米高度相对湿度（%）
        # precipitation: 累积降水量（毫米）
        "hourly": "relative_humidity_2m,precipitation",
        "timezone": "Asia/Shanghai",
    }

    try:
        resp = requests.get(url, params=params, timeout=45)
        resp.raise_for_status()
        raw = resp.json()
    except requests.exceptions.Timeout:
        raise ConnectionError("Open-Meteo API 请求超时（>45s），请检查网络后重试")
    except requests.exceptions.HTTPError as e:
        raise ConnectionError(f"Open-Meteo API HTTP 错误 {e.response.status_code}：{e}")
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Open-Meteo API 网络请求失败：{e}")
    except (KeyError, ValueError) as e:
        raise ValueError(f"Open-Meteo API 数据结构解析失败：{e}")

    if "hourly" not in raw or "time" not in raw.get("hourly", {}):
        raise ValueError("Open-Meteo API 返回数据格式异常，缺少 hourly 字段")

    hourly = raw["hourly"]
    df_hourly = pd.DataFrame({
        "datetime":     pd.to_datetime(hourly["time"]),
        "humidity":     hourly["relative_humidity_2m"],
        "precipitation": hourly["precipitation"],
    })

    # 过滤未来数据（API 可能返回 NaN 的未来时间步）
    df_hourly = df_hourly[df_hourly["datetime"] <= pd.Timestamp.now()].copy()

    # 聚合为月均 / 月总
    df_hourly["year_month"] = df_hourly["datetime"].dt.to_period("M")
    monthly = (
        df_hourly.groupby("year_month")
        .agg(
            avg_humidity=("humidity", "mean"),
            total_precipitation=("precipitation", "sum"),
        )
        .reset_index()
    )
    monthly["location"] = location_name
    monthly["year_month_dt"] = monthly["year_month"].dt.to_timestamp()
    monthly = monthly[monthly["avg_humidity"].notna()].copy()

    # 写入本地缓存
    try:
        monthly.to_parquet(cache_path, index=False)
    except Exception:
        pass  # 缓存写入失败不中断流程

    return monthly


# ══════════════════════════════════════════════════════════════════════════════
# 模块 2：UN Comtrade 贸易数据（双 API 备份 + 本地 CSV 缓存）
# ══════════════════════════════════════════════════════════════════════════════

# 目标国家代码映射
PARTNER_MAP = {
    "704": "越南",
    "458": "马来西亚",
    "360": "印度尼西亚",
}
# 查询年份：近五年完整年度数据
QUERY_YEARS = [2019, 2020, 2021, 2022, 2023]
# HS Code 283620：碳酸钠（纯碱 / Soda Ash / Disodium Carbonate）
HS_CODE = "283620"
# 中国海关代码
CHINA_CODE = "156"


def fetch_comtrade_data() -> pd.DataFrame:
    """
    从联合国商品贸易统计数据库获取中国纯碱出口数据。

    主接口：UN Comtrade Public Preview API v1
      https://comtradeapi.un.org/public/v1/preview/C/A/HS
    备用接口：UN Comtrade Legacy API
      https://comtrade.un.org/api/get

    数据品类：HS Code 283620（碳酸钠/纯碱）
    出口方：中国（Reporter Code 156）
    进口方：越南（704）、马来西亚（458）、印度尼西亚（360）
    时间范围：2019—2023 年（年度数据）

    本地缓存：每日首次请求后存为 real_trade_data.csv，当日后续加载缓存
    """
    # ── 检查今日缓存 ──────────────────────────────────────────────────────────
    if os.path.exists(TRADE_CACHE_FILE):
        mtime = datetime.fromtimestamp(os.path.getmtime(TRADE_CACHE_FILE)).date()
        if mtime == date.today():
            try:
                df = pd.read_csv(TRADE_CACHE_FILE)
                if len(df) > 0:
                    return df
            except Exception:
                pass

    records = []

    # ── 主接口：新版 UN Comtrade Public Preview API ───────────────────────────
    success_v1 = False
    for partner_code, partner_name in PARTNER_MAP.items():
        for year in QUERY_YEARS:
            try:
                url = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
                params = {
                    "reporterCode": CHINA_CODE,
                    "cmdCode":      HS_CODE,
                    "flowCode":     "X",          # X = 出口
                    "partnerCode":  partner_code,
                    "period":       str(year),
                }
                resp = requests.get(
                    url, params=params,
                    headers=BROWSER_HEADERS,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                if "data" in data and data["data"]:
                    for rec in data["data"]:
                        records.append({
                            "year":          year,
                            "partner_code":  partner_code,
                            "partner_name":  partner_name,
                            "net_weight_kg": rec.get("netWgt") or 0,
                            "trade_value_usd": rec.get("primaryValue") or 0,
                        })
                    success_v1 = True

                # Comtrade Public API 限速约 1 req/s
                time.sleep(1.2)

            except requests.exceptions.RequestException:
                # 单次失败不中断，继续其他年份
                time.sleep(2)
                continue

    # ── 备用接口：旧版 Legacy API（若新版全部失败）────────────────────────────
    if not success_v1:
        records = _fetch_comtrade_legacy()

    if not records:
        # 若两个接口均无响应，抛出详细错误
        raise ConnectionError(
            "UN Comtrade 新版 API（comtradeapi.un.org）与旧版 API（comtrade.un.org）"
            "均无响应。可能原因：\n"
            "① 网络连接受限，请检查防火墙/代理设置\n"
            "② UN Comtrade API 维护中（可访问 comtradeapi.un.org 验证）\n"
            "③ IP 被临时限速，建议 30 分钟后重试"
        )

    df = pd.DataFrame(records)
    # 净重单位：kg → 公吨（1 吨 = 1000 kg）
    df["net_weight_ton"] = (df["net_weight_kg"] / 1000).round(2)
    df["year"] = df["year"].astype(int)

    # 写入本地缓存
    try:
        df.to_csv(TRADE_CACHE_FILE, index=False, encoding="utf-8-sig")
    except Exception:
        pass

    return df


def _fetch_comtrade_legacy() -> list:
    """
    联合国 Comtrade 旧版 API（备用）
    端点：https://comtrade.un.org/api/get
    说明：旧版 API 已逐步迁移至新版，但部分历史数据仍可通过此接口查询
    """
    url = "https://comtrade.un.org/api/get"
    params = {
        "r":   CHINA_CODE,
        "p":   ",".join(PARTNER_MAP.keys()),
        "ps":  ",".join(str(y) for y in QUERY_YEARS),
        "px":  "HS",
        "cc":  HS_CODE,
        "rg":  "2",        # 2 = 出口
        "fmt": "json",
        "max": "500",
        "head": "H",
    }
    records = []
    try:
        resp = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "dataset" in data and data["dataset"]:
            for rec in data["dataset"]:
                pc = str(rec.get("ptCode", ""))
                records.append({
                    "year":           rec.get("yr", 0),
                    "partner_code":   pc,
                    "partner_name":   PARTNER_MAP.get(pc, "未知"),
                    "net_weight_kg":  rec.get("NetWeight") or 0,
                    "trade_value_usd": rec.get("TradeValue") or 0,
                })
    except Exception:
        pass
    return records


# ══════════════════════════════════════════════════════════════════════════════
# 模块 3：纯碱现货价格爬虫（多源备份）
# ══════════════════════════════════════════════════════════════════════════════

def scrape_soda_ash_price() -> dict:
    """
    从公开大宗商品信息平台实时爬取纯碱现货均价（元/吨）。

    主要数据源：
      ① 生意社 SunSirs — https://www.sunsirs.com/
         国内知名大宗商品价格数据库，日度更新
      ② 百川盈孚 — https://www.100ppi.com/
         化工品专业价格平台，作为备用数据源

    合理性验证：纯碱现货价格区间设定为 500—5000 元/吨
    （历史价格：2021 年低点约 1100，2022 年高点约 3600）

    返回
    ----
    dict  {
      "price": float | None,     # 当日现货均价（元/吨）
      "source": str,             # 数据来源名称
      "timestamp": str,          # 获取时间
      "error": str | None,       # 失败时的详细错误描述
      "note": str                # 额外说明
    }
    """
    result = {
        "price":     None,
        "source":    None,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "error":     None,
        "note":      "",
    }

    errors = []

    # ── 尝试①：生意社 SunSirs ───────────────────────────────────────────────
    try:
        price = _scrape_sunsirs()
        if price:
            result["price"]  = price
            result["source"] = "生意社 SunSirs（www.sunsirs.com）"
            return result
        else:
            errors.append("生意社：页面已加载，但未匹配到有效价格数字")
    except Exception as e:
        errors.append(f"生意社：{type(e).__name__} — {e}")

    # ── 尝试②：百川盈孚 100ppi ──────────────────────────────────────────────
    try:
        price = _scrape_100ppi()
        if price:
            result["price"]  = price
            result["source"] = "百川盈孚（www.100ppi.com）"
            result["error"]  = None
            return result
        else:
            errors.append("百川盈孚：页面已加载，但未匹配到有效价格数字")
    except Exception as e:
        errors.append(f"百川盈孚：{type(e).__name__} — {e}")

    # ── 所有数据源失败 ────────────────────────────────────────────────────────
    result["error"] = " | ".join(errors)
    result["note"]  = (
        "爬虫失败原因可能包括：网站结构调整、IP 封锁或网络限制。"
        "已在侧边栏提供手动调价滑块，请根据实际市场行情手动输入。"
    )
    return result


def _scrape_sunsirs() -> float | None:
    """
    生意社纯碱现货报价页面爬虫
    目标 URL：https://www.sunsirs.com/uk/detail-commodity-1099.html（轻质纯碱）
    备用 URL：https://www.sunsirs.com/uk/detail-commodity-1102.html（重质纯碱）
    """
    targets = [
        # (URL, 商品描述)
        ("https://www.sunsirs.com/uk/detail-commodity-1099.html", "轻质纯碱"),
        ("https://www.sunsirs.com/uk/detail-commodity-1102.html", "重质纯碱"),
    ]
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    for url, label in targets:
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            # 生意社价格通常出现在 .price_table 或 .commodity-price 相关 class 中
            # 同时通过正则全文搜索"元/吨"附近数字作为兜底方案
            price = _extract_price_from_soup(soup, label)
            if price:
                return price
        except Exception:
            continue
    return None


def _scrape_100ppi() -> float | None:
    """
    百川盈孚纯碱价格页面爬虫
    目标 URL：https://www.100ppi.com/price/detail-1-1157.html（纯碱现货价格）
    """
    urls = [
        "https://www.100ppi.com/price/detail-1-1157.html",
        "https://www.100ppi.com/sf/detail-1-1157.html",
    ]
    session = requests.Session()
    session.headers.update({**BROWSER_HEADERS, "Referer": "https://www.100ppi.com/"})

    for url in urls:
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            price = _extract_price_from_soup(soup, "纯碱")
            if price:
                return price
        except Exception:
            continue
    return None


def _extract_price_from_soup(soup: BeautifulSoup, keyword: str) -> float | None:
    """
    从 BeautifulSoup 解析结果中提取合理的大宗商品价格。

    策略（按优先级）：
    1. 查找包含"元/吨"文本的 <td> 或 <span> 元素
    2. 查找 class 包含 "price" 的元素中的数字
    3. 全文搜索"元/吨"附近的三到四位数字
    """
    # 策略 1：精准匹配"元/吨"相邻单元格
    price_pattern = re.compile(r"(\d{3,5}(?:\.\d{1,2})?)\s*元[/／]吨")
    full_text = soup.get_text(separator="\n")
    matches = price_pattern.findall(full_text)
    valid_prices = [float(p) for p in matches if 500.0 <= float(p) <= 5000.0]
    if valid_prices:
        # 取出现频率最高的价格（去除异常值）
        from statistics import median
        return round(median(valid_prices), 2)

    # 策略 2：class 包含 price 的元素
    for tag in soup.find_all(attrs={"class": re.compile(r"price", re.I)}):
        text = tag.get_text(strip=True)
        m = re.search(r"(\d{3,5}(?:\.\d{1,2})?)", text)
        if m:
            v = float(m.group(1))
            if 500.0 <= v <= 5000.0:
                return v

    # 策略 3：宽泛正则，任意"数字 + 元"结构
    loose_pattern = re.compile(r"(\d{3,5}(?:\.\d{1,2})?)\s*元")
    loose_matches = loose_pattern.findall(full_text)
    valid_loose = [float(p) for p in loose_matches if 500.0 <= float(p) <= 5000.0]
    if valid_loose:
        from statistics import median
        return round(median(valid_loose), 2)

    return None
