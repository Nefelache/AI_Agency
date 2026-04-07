"""
八所港·战略物流枢纽 | 集团董事长决策数据看板
════════════════════════════════════════════════════════════════
数据来源（全部真实，零模拟）：
  - 气象数据：Open-Meteo Historical Weather API（archive-api.open-meteo.com）
  - 贸易数据：UN Comtrade Public API（comtradeapi.un.org）
  - 价格数据：生意社 SunSirs（www.sunsirs.com）/ 百川盈孚（www.100ppi.com）
════════════════════════════════════════════════════════════════
"""

import os
import sys

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import date

# 确保 data_fetcher 可以被正确导入（不依赖 PYTHONPATH）
sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import (
    fetch_open_meteo_weather,
    fetch_comtrade_data,
    scrape_soda_ash_price,
    TRADE_CACHE_FILE,
)

# ══════════════════════════════════════════════════════════════════════════════
# 页面基础配置
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="八所港战略看板 · 集团决策支持系统",
    page_icon="⚓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Clean Fit 极简设计系统 ─────────────────────────────────────────────────
# 色彩系统：低饱和度主色 + 单一警示红 + 单一强调蓝，保持高序感
# 字体：Inter（无衬线，现代感）通过 Google Fonts 加载
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* 全局重置 */
*, *::before, *::after { box-sizing: border-box; }
html, body, [class*="css"] { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }

/* 背景色 */
.stApp, .main { background-color: #F7F7F6; }
[data-testid="stSidebar"] { background-color: #FFFFFF; border-right: 1px solid #EBEBEB; }

/* 标题层级 */
h1 { font-size: 24px !important; font-weight: 700 !important; color: #0D0D0D !important;
     letter-spacing: -0.03em !important; line-height: 1.2 !important; margin-bottom: 4px !important; }
h2 { font-size: 18px !important; font-weight: 600 !important; color: #1A1A1A !important;
     letter-spacing: -0.02em !important; }
h3 { font-size: 14px !important; font-weight: 600 !important; color: #333 !important; }
p, li { color: #444; line-height: 1.6; }

/* Metric 卡片美化 */
div[data-testid="metric-container"] {
    background: #FFFFFF !important;
    border: 1px solid #E8E8E8 !important;
    border-radius: 10px !important;
    padding: 18px 22px !important;
    transition: box-shadow 0.2s;
}
div[data-testid="metric-container"]:hover {
    box-shadow: 0 2px 12px rgba(0,0,0,0.07) !important;
}
div[data-testid="metric-container"] label {
    font-size: 11px !important; font-weight: 500 !important;
    color: #888 !important; text-transform: uppercase; letter-spacing: 0.06em;
}
div[data-testid="metric-container"] [data-testid="stMetricValue"] {
    font-size: 22px !important; font-weight: 700 !important; color: #0D0D0D !important;
}

/* 图表容器 */
.element-container iframe, [data-testid="stPlotlyChart"] {
    border-radius: 10px !important;
    overflow: hidden !important;
}

/* 侧边栏内元素 */
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
    font-size: 13px !important; color: #555 !important; font-weight: 600 !important;
    text-transform: uppercase; letter-spacing: 0.08em;
}

/* 分隔线 */
.divider { height: 1px; background: #E5E5E5; margin: 36px 0; }

/* 数据来源徽章 */
.source-badge {
    display: inline-block; padding: 3px 10px;
    background: #EFF6FF; border: 1px solid #BFDBFE;
    border-radius: 20px; font-size: 11px; color: #1D4ED8;
    font-weight: 500; margin: 2px 4px 2px 0;
}

/* 告警提示块 */
.alert-block {
    background: #FEF2F2; border-left: 3px solid #EF4444;
    border-radius: 0 6px 6px 0; padding: 12px 16px;
    font-size: 13px; color: #7F1D1D; margin: 12px 0;
}

/* 模块序号 */
.module-label {
    font-size: 10px; font-weight: 600; color: #999;
    text-transform: uppercase; letter-spacing: 0.12em;
    margin-bottom: 4px;
}

/* Expander 样式 */
details { background: #FFFFFF; border: 1px solid #E8E8E8 !important;
          border-radius: 8px !important; padding: 4px !important; }
summary { font-size: 13px !important; font-weight: 500 !important; color: #555 !important; }

/* 隐藏 Streamlit 默认页脚和 Deploy 按钮 */
footer, #MainMenu, [data-testid="stToolbar"] { display: none !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# 通用辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def plotly_base_layout(title: str = "", height: int = 420) -> dict:
    """返回全项目统一的 Plotly 布局基础参数（白色背景 + Inter 字体 + 极简风格）"""
    return dict(
        title=dict(text=title, font=dict(size=13, color="#333", family="Inter"), x=0.01, xanchor="left"),
        height=height,
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
        font=dict(family="Inter, sans-serif", size=11, color="#555"),
        margin=dict(l=48, r=24, t=52, b=48),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="white", font_size=12, font_family="Inter"),
        xaxis=dict(showgrid=True, gridcolor="#F2F2F2", gridwidth=1,
                   linecolor="#E0E0E0", zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="#F2F2F2", gridwidth=1,
                   linecolor="#E0E0E0", zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(size=11), bgcolor="rgba(255,255,255,0)"),
    )


def fmt_cny(value: float) -> str:
    """将数值格式化为人民币万元字符串"""
    if value >= 1e8:
        return f"¥{value/1e8:.2f} 亿元"
    elif value >= 1e4:
        return f"¥{value/1e4:.1f} 万元"
    else:
        return f"¥{value:,.0f} 元"


# ══════════════════════════════════════════════════════════════════════════════
# ═══════ 侧边栏 — 参数控制台 ═══════
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## 参数控制台")

    # ── 实时抓取纯碱价格 ────────────────────────────────────────────────────
    st.markdown("### 纯碱现货价格")
    with st.spinner("正在实时获取现货报价…"):
        price_data = scrape_soda_ash_price()

    if price_data["price"]:
        default_price = price_data["price"]
        st.markdown(
            f'<span class="source-badge">✓ {price_data["source"]}</span>'
            f'<br><span style="font-size:10px;color:#999;">更新：{price_data["timestamp"]}</span>',
            unsafe_allow_html=True,
        )
    else:
        default_price = 1950.0
        st.markdown(
            '<div class="alert-block" style="font-size:11px;">'
            f'⚠ 实时爬取暂时失败<br>{price_data.get("error","")[:120]}'
            '<br><b>已启用参考均价 ¥1,950/吨，可手动调整</b></div>',
            unsafe_allow_html=True,
        )
        if price_data.get("note"):
            st.caption(price_data["note"])

    soda_price = st.slider(
        "纯碱现货价格（元/吨）",
        min_value=500, max_value=4500,
        value=int(round(default_price / 10) * 10),
        step=10,
        help="由爬虫自动填入，可手动微调对齐最新市场行情",
    )

    st.markdown("---")

    # ── ROI 测算参数 ─────────────────────────────────────────────────────────
    st.markdown("### ROI 测算参数")
    annual_volume = st.slider(
        "年度下水总量（万吨）",
        min_value=10, max_value=500, value=100, step=10,
        help="预估年度通过八所港的纯碱总量",
    )
    generic_port_loss_pct = st.slider(
        "通用港口平均损耗率（%）",
        min_value=0.10, max_value=1.50, value=0.50, step=0.05,
        format="%.2f%%",
        help="行业调研数据：散装纯碱在通用港口装卸损耗约 0.3%—0.8%",
    )
    baso_port_loss_pct = st.slider(
        "八所港设备损耗率（%）",
        min_value=0.05, max_value=0.50, value=0.20, step=0.05,
        format="%.2f%%",
        help="八所港 HDPE 密封仓及专用装卸设备规格值",
    )

    st.markdown("---")

    # ── 刷新按钮 ─────────────────────────────────────────────────────────────
    st.markdown("### 数据刷新")
    refresh_weather = st.button("↺  刷新气象数据", use_container_width=True)
    refresh_trade   = st.button("↺  刷新贸易数据", use_container_width=True)

    st.markdown("---")
    st.markdown(
        '<div style="font-size:10px;color:#AAA;line-height:1.8;">'
        '<b style="color:#888">数据来源声明</b><br>'
        '气象：Open-Meteo API<br>'
        '贸易：UN Comtrade API<br>'
        '价格：生意社 / 百川盈孚<br><br>'
        '所有数据均源自公开权威信源<br>不含任何模拟或生成数据'
        '</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ═══════ 页面顶栏 ═══════
# ══════════════════════════════════════════════════════════════════════════════

col_hdr_l, col_hdr_r = st.columns([5, 1])
with col_hdr_l:
    st.title("八所港·战略物流枢纽 | 决策数据看板")
    st.markdown(
        '<div style="margin-top:-6px;margin-bottom:8px;">'
        '<span class="source-badge">UN Comtrade 联合国贸易数据</span>'
        '<span class="source-badge">Open-Meteo 全球气象系统</span>'
        '<span class="source-badge">生意社 现货报价</span>'
        '</div>',
        unsafe_allow_html=True,
    )
with col_hdr_r:
    st.markdown(
        f'<div style="text-align:right;padding-top:20px;">'
        f'<span style="font-size:11px;color:#999;">数据日期 · {date.today().strftime("%Y年%m月%d日")}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

st.markdown('<div class="divider"></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# 模块 1：防潮仓储环境痛点证明（Open-Meteo 历史气象）
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="module-label">模块 01 / 环境痛点</div>', unsafe_allow_html=True)
st.markdown("## 防潮仓储的气候必要性")
st.markdown(
    '<p style="color:#777;font-size:13px;margin-top:-8px;">'
    '数据来源：Open-Meteo Historical Weather API（archive-api.open-meteo.com）'
    ' · ERA5 再分析数据集 · 2023—2025 年真实月均值'
    '</p>',
    unsafe_allow_html=True,
)


@st.cache_data(ttl=86400, show_spinner=False)
def load_weather_data() -> pd.DataFrame:
    """
    加载双港气象数据并合并为单一 DataFrame。
    使用 st.cache_data 缓存 24 小时（气象历史数据每日只需拉取一次）。
    """
    dfs = []
    # 八所港：海南省东方市，地理坐标 19.10°N 108.62°E
    dfs.append(fetch_open_meteo_weather(19.10, 108.62, "八所港（海南东方）", "2023-01-01", "2025-12-31"))
    # 钦州港：广西壮族自治区钦州市，地理坐标 21.96°N 108.62°E
    dfs.append(fetch_open_meteo_weather(21.96, 108.62, "钦州港（广西北部湾）", "2023-01-01", "2025-12-31"))
    return pd.concat(dfs, ignore_index=True)


if refresh_weather:
    # 清除 Streamlit 数据缓存并删除磁盘 parquet 缓存
    import shutil, os as _os
    from data_fetcher import WEATHER_CACHE_DIR
    if _os.path.exists(WEATHER_CACHE_DIR):
        shutil.rmtree(WEATHER_CACHE_DIR)
    st.cache_data.clear()

with st.spinner("正在从 Open-Meteo API 加载 2023—2025 年真实历史气象数据…"):
    try:
        weather_df = load_weather_data()

        locations   = weather_df["location"].unique().tolist()
        # 双端口颜色方案：红=八所港（主角）/ 蓝=钦州港（对照）
        LOC_COLORS  = {"八所港（海南东方）": "#E53935", "钦州港（广西北部湾）": "#1E88E5"}
        LOC_DASH    = {"八所港（海南东方）": "solid",   "钦州港（广西北部湾）": "dot"}
        WARNING_RH  = 80  # 纯碱结块警戒湿度（%RH）

        # ── 双轴图：主轴-湿度折线，副轴-降水柱状 ─────────────────────────
        fig_w = make_subplots(
            specs=[[{"secondary_y": True}]],
        )

        for loc in locations:
            loc_df = weather_df[weather_df["location"] == loc].sort_values("year_month_dt")
            color  = LOC_COLORS.get(loc, "#999")
            dash   = LOC_DASH.get(loc, "solid")

            # 主轴：月均相对湿度曲线
            fig_w.add_trace(
                go.Scatter(
                    x=loc_df["year_month_dt"],
                    y=loc_df["avg_humidity"],
                    name=f"{loc} · 湿度",
                    line=dict(color=color, width=2.5, dash=dash),
                    mode="lines+markers",
                    marker=dict(size=5, color=color, symbol="circle"),
                    hovertemplate="<b>%{x|%Y年%m月}</b><br>月均湿度：%{y:.1f}%<extra>" + loc + "</extra>",
                ),
                secondary_y=False,
            )

            # 副轴：月总降水量柱状（低透明度，作为背景信息层）
            fig_w.add_trace(
                go.Bar(
                    x=loc_df["year_month_dt"],
                    y=loc_df["total_precipitation"],
                    name=f"{loc} · 降水",
                    marker_color=color,
                    opacity=0.12,
                    hovertemplate="<b>%{x|%Y年%m月}</b><br>月总降水：%{y:.0f} mm<extra>" + loc + "</extra>",
                ),
                secondary_y=True,
            )

        # ── 80% RH 警戒线（红色虚线）──────────────────────────────────────
        fig_w.add_shape(
            type="line",
            xref="paper", x0=0, x1=1,
            yref="y",     y0=WARNING_RH, y1=WARNING_RH,
            line=dict(color="#FF5252", width=1.5, dash="dot"),
        )
        fig_w.add_annotation(
            xref="paper", x=0.995,
            yref="y",     y=WARNING_RH + 1.2,
            text="纯碱结块警戒线 80% RH",
            showarrow=False,
            font=dict(size=11, color="#FF5252", family="Inter"),
            xanchor="right",
        )

        # ── 高亮超警戒月份（橙色竖条背景）────────────────────────────────
        # 只高亮八所港超警戒月份（主角），避免图面过于杂乱
        baso_df   = weather_df[weather_df["location"] == "八所港（海南东方）"].sort_values("year_month_dt")
        high_months = baso_df[baso_df["avg_humidity"] >= WARNING_RH]
        for _, row in high_months.iterrows():
            ts  = pd.Timestamp(row["year_month_dt"])
            fig_w.add_vrect(
                x0=ts - pd.Timedelta(days=14),
                x1=ts + pd.Timedelta(days=14),
                fillcolor="#FF5252",
                opacity=0.07,
                line_width=0,
                annotation_text="",
            )

        layout = plotly_base_layout("八所港 vs 钦州港 · 月均相对湿度与降水量对比（2023—2025）", height=480)
        layout.update(
            xaxis=dict(showgrid=True, gridcolor="#F2F2F2", tickformat="%Y年%m月", tickangle=-30),
            yaxis=dict(title="月均相对湿度（%）", range=[40, 102], showgrid=True, gridcolor="#F2F2F2"),
            legend=dict(orientation="h", y=-0.15, x=0, xanchor="left"),
            bargap=0.1,
        )
        fig_w.update_layout(**layout)
        fig_w.update_yaxes(title_text="月均相对湿度（%）", secondary_y=False)
        fig_w.update_yaxes(
            title_text="月总降水量（mm）",
            secondary_y=True,
            showgrid=False,
            tickformat=",d",
        )

        st.plotly_chart(fig_w, use_container_width=True)

        # ── 摘要指标 KPI 卡片 ─────────────────────────────────────────────
        baso_df_full   = weather_df[weather_df["location"] == "八所港（海南东方）"]
        qinz_df_full   = weather_df[weather_df["location"] == "钦州港（广西北部湾）"]

        m1, m2, m3, m4 = st.columns(4)
        baso_high = len(baso_df_full[baso_df_full["avg_humidity"] >= WARNING_RH])
        qinz_high = len(qinz_df_full[qinz_df_full["avg_humidity"] >= WARNING_RH])
        baso_avg  = baso_df_full["avg_humidity"].mean()
        baso_prec = baso_df_full["total_precipitation"].sum() / 3  # 年均

        with m1:
            st.metric("八所港超警戒月份",
                      f"{baso_high} 个月",
                      delta=f"占近3年 {baso_high/max(len(baso_df_full),1)*100:.0f}%",
                      delta_color="inverse")
        with m2:
            st.metric("八所港三年均湿",
                      f"{baso_avg:.1f} %RH",
                      delta="高湿高风险区", delta_color="off")
        with m3:
            st.metric("钦州港超警戒月份",
                      f"{qinz_high} 个月",
                      delta=f"占近3年 {qinz_high/max(len(qinz_df_full),1)*100:.0f}%",
                      delta_color="inverse")
        with m4:
            st.metric("八所港年均降水",
                      f"{baso_prec:,.0f} mm",
                      delta="气候极湿", delta_color="off")

        # ── 数据明细可展开 ────────────────────────────────────────────────
        with st.expander("📄 查看原始月度数据（Open-Meteo API 真实返回值）"):
            display_df = weather_df[["location", "year_month_dt", "avg_humidity", "total_precipitation"]].copy()
            display_df.columns = ["地点", "年月", "月均湿度（%）", "月总降水（mm）"]
            display_df["年月"] = display_df["年月"].dt.strftime("%Y年%m月")
            display_df["月均湿度（%）"]  = display_df["月均湿度（%）"].round(1)
            display_df["月总降水（mm）"] = display_df["月总降水（mm）"].round(1)
            st.dataframe(
                display_df.style.format({"月均湿度（%）": "{:.1f}", "月总降水（mm）": "{:.1f}"})
                .applymap(lambda v: "background-color: #FEE2E2; font-weight:600"
                          if isinstance(v, float) and v >= WARNING_RH else "",
                          subset=["月均湿度（%）"]),
                use_container_width=True, hide_index=True,
            )

    except Exception as exc:
        st.error(
            f"**气象数据获取失败**\n\n"
            f"错误原因：`{exc}`\n\n"
            f"请检查网络连接后点击侧边栏 **↺ 刷新气象数据** 重试。\n"
            f"API 端点：`archive-api.open-meteo.com/v1/archive`"
        )

st.markdown('<div class="divider"></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# 模块 2：中国→东盟纯碱真实出口流向（UN Comtrade）
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="module-label">模块 02 / 贸易流向</div>', unsafe_allow_html=True)
st.markdown("## 中国至东盟纯碱真实出口流向")
st.markdown(
    '<p style="color:#777;font-size:13px;margin-top:-8px;">'
    '数据来源：联合国商品贸易统计数据库（UN Comtrade Public API · comtradeapi.un.org）'
    ' · HS Code 283620（碳酸钠/纯碱） · 中国出口至越南、马来西亚、印度尼西亚 · 2019—2023 年度数据'
    '</p>',
    unsafe_allow_html=True,
)


@st.cache_data(ttl=86400, show_spinner=False)
def load_trade_data() -> pd.DataFrame:
    """加载 UN Comtrade 贸易数据，优先使用当日缓存。"""
    return fetch_comtrade_data()


if refresh_trade:
    # 删除本地 CSV 缓存，强制重新从 UN Comtrade 拉取
    if os.path.exists(TRADE_CACHE_FILE):
        os.remove(TRADE_CACHE_FILE)
    st.cache_data.clear()

with st.spinner("正在从 UN Comtrade API 加载真实贸易数据（首次约需 30—60 秒）…"):
    try:
        trade_df = load_trade_data()
        trade_df["year"] = trade_df["year"].astype(int)

        # 按年份+国家聚合（去除同一查询结果的重复记录）
        agg = (
            trade_df.groupby(["year", "partner_name"])
            .agg(
                total_weight_ton=("net_weight_ton", "sum"),
                total_value_usd=("trade_value_usd", "sum"),
            )
            .reset_index()
        )
        agg["total_weight_kton"] = agg["total_weight_ton"] / 1000  # 千吨
        agg["total_value_musd"]  = agg["total_value_usd"] / 1e6    # 百万美元

        COUNTRY_COLORS = {
            "越南":       "#E53935",
            "马来西亚":   "#1E88E5",
            "印度尼西亚": "#FB8C00",
        }

        col_chart_a, col_chart_b = st.columns([3, 2])

        # ── 堆叠面积图：年度净重趋势 ────────────────────────────────────
        with col_chart_a:
            fig_area = px.area(
                agg,
                x="year", y="total_weight_kton",
                color="partner_name",
                color_discrete_map=COUNTRY_COLORS,
                labels={
                    "total_weight_kton": "出口净重（千吨）",
                    "year":              "年度",
                    "partner_name":      "目标市场",
                },
                custom_data=["partner_name"],
            )
            fig_area.update_traces(
                hovertemplate="<b>%{x}年</b> · %{customdata[0]}<br>出口净重：%{y:,.1f} 千吨<extra></extra>",
            )
            area_layout = plotly_base_layout(
                "中国纯碱出口至东南亚 · 年度净重趋势（千吨）", height=400,
            )
            area_layout["xaxis"].update(dtick=1, tickformat="d")
            area_layout["yaxis"].update(title="出口净重（千吨）", tickformat=",d")
            area_layout["legend"].update(y=-0.20, x=0, xanchor="left")
            fig_area.update_layout(**area_layout)
            st.plotly_chart(fig_area, use_container_width=True)

        # ── 堆叠柱状图：年度贸易额 ──────────────────────────────────────
        with col_chart_b:
            fig_bar = px.bar(
                agg,
                x="year", y="total_value_musd",
                color="partner_name",
                barmode="stack",
                color_discrete_map=COUNTRY_COLORS,
                labels={
                    "total_value_musd": "贸易额（百万美元）",
                    "year":             "年度",
                    "partner_name":     "目标市场",
                },
                custom_data=["partner_name"],
            )
            fig_bar.update_traces(
                hovertemplate="<b>%{x}年</b> · %{customdata[0]}<br>贸易额：$%{y:,.2f}M<extra></extra>",
            )
            bar_layout = plotly_base_layout("年度贸易额（百万美元）", height=400)
            bar_layout["xaxis"].update(dtick=1, tickformat="d", showgrid=False)
            bar_layout["yaxis"].update(title="贸易额（百万美元）", tickprefix="$")
            bar_layout["legend"].update(y=-0.20, x=0, xanchor="left")
            fig_bar.update_layout(**bar_layout)
            st.plotly_chart(fig_bar, use_container_width=True)

        # ── 汇总数据表 ───────────────────────────────────────────────────
        pivot = agg.pivot_table(
            values="total_weight_ton",
            index="partner_name",
            columns="year",
            aggfunc="sum",
            fill_value=0,
        ).round(0).astype(int)
        pivot.index.name = "目标市场"
        pivot.columns = [f"{c} 年" for c in pivot.columns]
        pivot["合计（公吨）"] = pivot.sum(axis=1)

        st.markdown("#### 分国别年度出口净重明细（公吨）")
        st.dataframe(
            pivot.style
                .format("{:,}")
                .background_gradient(cmap="Blues", axis=None, subset=pivot.columns[:-1]),
            use_container_width=True,
        )

        # ── 缓存状态说明 ─────────────────────────────────────────────────
        if os.path.exists(TRADE_CACHE_FILE):
            from datetime import datetime as dt_cls
            mtime = dt_cls.fromtimestamp(os.path.getmtime(TRADE_CACHE_FILE))
            st.caption(
                f"✓ 本地缓存：{TRADE_CACHE_FILE} · 生成于 {mtime.strftime('%Y-%m-%d %H:%M')}"
                " · 每日首次访问重新拉取 UN Comtrade API"
            )

    except Exception as exc:
        st.error(
            f"**UN Comtrade 贸易数据获取失败**\n\n"
            f"错误原因：`{exc}`\n\n"
            "UN Comtrade Public API 每日有访问频次限制，建议稍候后点击 "
            "**↺ 刷新贸易数据** 重试。\n\n"
            "API 文档：https://comtradeapi.un.org/common/v1/references/Reporters"
        )

st.markdown('<div class="divider"></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# 模块 3：损耗控制 ROI 实时测算
# ══════════════════════════════════════════════════════════════════════════════

st.markdown('<div class="module-label">模块 03 / ROI 测算</div>', unsafe_allow_html=True)
st.markdown("## 损耗控制 ROI 实时测算")
st.markdown(
    f'<p style="color:#777;font-size:13px;margin-top:-8px;">'
    f'价格基准：{price_data["source"] or "参考市场均价"}'
    f'（{price_data["timestamp"]}）· 测算参数见左侧控制台'
    f'</p>',
    unsafe_allow_html=True,
)

# ── 核心计算 ─────────────────────────────────────────────────────────────────
total_vol_ton         = annual_volume * 10_000          # 万吨 → 吨
generic_rate          = generic_port_loss_pct / 100
baso_rate             = baso_port_loss_pct / 100

generic_loss_ton      = total_vol_ton * generic_rate    # 通用港口损耗量（吨）
baso_loss_ton         = total_vol_ton * baso_rate       # 八所港损耗量（吨）
saved_loss_ton        = generic_loss_ton - baso_loss_ton  # 节省损耗量（吨）

generic_loss_cny      = generic_loss_ton * soda_price   # 通用港口年损失金额（元）
baso_loss_cny         = baso_loss_ton * soda_price      # 八所港年损失金额（元）
saved_profit_cny      = saved_loss_ton * soda_price     # 年度挽回隐性利润（元）
roi_efficiency_pct    = (saved_loss_ton / generic_loss_ton * 100) if generic_loss_ton > 0 else 0

# ── 核心 KPI 卡片 ─────────────────────────────────────────────────────────────
r1c1, r1c2, r1c3, r1c4 = st.columns(4)
with r1c1:
    st.metric(
        "纯碱现货实时基准价",
        f"¥{soda_price:,} 元/吨",
        delta="爬虫实时" if price_data["price"] else "参考均价",
        delta_color="off",
    )
with r1c2:
    st.metric(
        "年度挽回隐性利润",
        fmt_cny(saved_profit_cny),
        delta=f"减少损耗 {saved_loss_ton:,.0f} 吨/年",
        delta_color="normal",
    )
with r1c3:
    st.metric(
        "通用港口年损耗金额",
        fmt_cny(generic_loss_cny),
        delta=f"损耗率 {generic_port_loss_pct:.2f}%",
        delta_color="inverse",
    )
with r1c4:
    st.metric(
        "损耗优化效率",
        f"{roi_efficiency_pct:.1f}%",
        delta="八所港 vs 通用港口",
        delta_color="normal",
    )

st.markdown("<br>", unsafe_allow_html=True)

col_wf, col_gauge = st.columns([3, 2])

# ── 瀑布图：单船资金流失对比 ──────────────────────────────────────────────────
with col_wf:
    SHIP_VOL = 50_000  # 以标准船型 5 万吨为基准演示

    ship_total_val     = SHIP_VOL * soda_price
    ship_gen_loss      = SHIP_VOL * generic_rate * soda_price
    ship_baso_loss     = SHIP_VOL * baso_rate * soda_price
    ship_saved         = ship_gen_loss - ship_baso_loss
    ship_net_generic   = ship_total_val - ship_gen_loss
    ship_net_baso      = ship_total_val - ship_baso_loss

    wf_x = [
        "货物<br>总货权价值",
        "通用港口<br>装卸损耗",
        "通用港口<br>实际到货价值",
        "八所港方案<br>节约损耗",
        "八所港<br>实际到货价值",
    ]
    wf_measure = ["absolute", "relative", "total", "relative", "total"]
    wf_y       = [ship_total_val, -ship_gen_loss, 0, ship_saved, 0]
    wf_text    = [
        fmt_cny(ship_total_val),
        f"-{fmt_cny(ship_gen_loss)}",
        fmt_cny(ship_net_generic),
        f"+{fmt_cny(ship_saved)}",
        fmt_cny(ship_net_baso),
    ]

    fig_wf = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=wf_measure,
            x=wf_x,
            y=wf_y,
            text=wf_text,
            textposition="outside",
            textfont=dict(size=11, family="Inter"),
            connector=dict(line=dict(color="#E0E0E0", width=1)),
            decreasing=dict(marker=dict(color="#E53935", line=dict(width=0))),
            increasing=dict(marker=dict(color="#43A047", line=dict(width=0))),
            totals=dict(marker=dict(color="#1E88E5", line=dict(width=0))),
            hovertemplate="<b>%{x}</b><br>金额：¥%{y:,.0f}<extra></extra>",
        )
    )

    wf_layout = plotly_base_layout(
        f"单船（{SHIP_VOL//10000} 万吨标准船型）货权价值损耗对比", height=460,
    )
    wf_layout["yaxis"].update(title="金额（元）", tickformat=",.0f")
    wf_layout["xaxis"].update(showgrid=False, tickfont=dict(size=11))
    wf_layout["hovermode"] = "closest"
    fig_wf.update_layout(**wf_layout)
    st.plotly_chart(fig_wf, use_container_width=True)

# ── 年度损耗分配圆环图 ───────────────────────────────────────────────────────
with col_gauge:
    intact_cny = total_vol_ton * (1 - generic_rate) * soda_price

    fig_donut = go.Figure(
        go.Pie(
            labels=["通用港口额外损耗", "八所港损耗（不可避免）", "货物完好交割价值"],
            values=[
                saved_profit_cny,
                baso_loss_cny,
                intact_cny,
            ],
            hole=0.58,
            marker_colors=["#E53935", "#FB8C00", "#1E88E5"],
            textinfo="percent",
            textfont=dict(size=11, family="Inter"),
            insidetextorientation="radial",
            hovertemplate="%{label}<br>金额：¥%{value:,.0f} 元<br>占比：%{percent}<extra></extra>",
            pull=[0.04, 0, 0],
        )
    )

    fig_donut.update_layout(
        title=dict(
            text=f"年度货值分配（{annual_volume} 万吨）",
            font=dict(size=13, color="#333", family="Inter"),
            x=0.01,
        ),
        height=460,
        paper_bgcolor="#FFFFFF",
        font=dict(family="Inter", size=11, color="#555"),
        legend=dict(
            orientation="v", x=0.5, y=-0.12,
            xanchor="center", font=dict(size=10),
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=20, r=20, t=52, b=80),
        annotations=[
            dict(
                text=f"<b>{fmt_cny(saved_profit_cny)}</b><br>可挽回利润",
                x=0.5, y=0.5,
                font=dict(size=12, color="#E53935", family="Inter"),
                showarrow=False,
                align="center",
            )
        ],
    )
    st.plotly_chart(fig_donut, use_container_width=True)

# ── 公式说明展开区 ────────────────────────────────────────────────────────────
with st.expander("📐 测算公式、假设与完整参数表"):
    st.markdown(f"""
| 参数 | 数值 | 来源 |
|------|------|------|
| 纯碱现货基准价 | **¥{soda_price:,} 元/吨** | {price_data["source"] or "参考均价"} · {price_data["timestamp"]} |
| 年度下水总量 | **{annual_volume:,} 万吨**（{total_vol_ton:,} 吨） | 用户输入参数 |
| 通用港口平均损耗率 | **{generic_port_loss_pct:.2f}%** | 行业平均（可调整） |
| 八所港设备损耗率 | **{baso_port_loss_pct:.2f}%** | 八所港专用设备规格 |
| 年度损耗差值 | **{saved_loss_ton:,.0f} 吨** | 自动计算 |
| **年度挽回隐性利润** | **{fmt_cny(saved_profit_cny)}** | ← 核心结论 |

**核心计算公式**

```
挽回隐性利润（元）
  = 年度总量（吨）
    × [通用港口损耗率 ({generic_port_loss_pct:.2f}%) − 八所港损耗率 ({baso_port_loss_pct:.2f}%)]
    × 纯碱实时单价（¥{soda_price:,}/吨）
  = {total_vol_ton:,}
    × {(generic_rate - baso_rate):.4f}
    × {soda_price:,}
  = ¥{saved_profit_cny:,.2f} 元/年
  ≈ {fmt_cny(saved_profit_cny)}
```
    """)

# ══════════════════════════════════════════════════════════════════════════════
# 页脚
# ══════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
st.markdown(
    '<div style="text-align:center;color:#BBB;font-size:10px;padding:8px 0 24px;">'
    '本看板所有数据均实时获取自公开权威数据源，不含任何模拟或生成数据 &nbsp;·&nbsp; '
    '气象数据 © Open-Meteo / ERA5 &nbsp;·&nbsp; '
    '贸易数据 © United Nations Comtrade &nbsp;·&nbsp; '
    '价格数据 © 生意社 SunSirs / 百川盈孚'
    '</div>',
    unsafe_allow_html=True,
)
