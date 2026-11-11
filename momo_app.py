import re
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from curl_cffi import requests

# ---------------------------------------------------------------
# 基本設定
# ---------------------------------------------------------------
st.set_page_config(page_title="MOMO 商品價格分析", page_icon="🛒", layout="wide")

API_URL = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
HOME_URL = "https://www.momoshop.com.tw/"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}


# ---------------------------------------------------------------
# 爬蟲
# ---------------------------------------------------------------
def build_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


def extract_brand(name: str) -> str:
    """優先取【】內文字當品牌，否則取品名第一個詞。"""
    m = re.search(r"【(.+?)】", name)
    if m:
        return m.group(1).strip()
    first = re.split(r"[\s/／,，]", name.strip())[0] if name.strip() else ""
    return first[:10] or "未知"


def scrape(keyword: str, pages: int, delay: float = 1.0) -> pd.DataFrame:
    session = requests.Session(impersonate="chrome")  # 模擬 Chrome TLS 指紋
    session.get(HOME_URL, headers=HEADERS)  # 先取得 cookie

    rows = []
    progress = st.progress(0.0, text="開始爬取…")
    for page in range(1, pages + 1):
        progress.progress((page - 1) / pages, text=f"爬取第 {page}/{pages} 頁…")
        try:
            resp = session.post(API_URL, headers=HEADERS, json=build_payload(keyword, page))
        except Exception as e:
            st.warning(f"第 {page} 頁請求失敗：{e}")
            break

        if resp.status_code != 200:
            st.warning(f"第 {page} 頁回應狀態 {resp.status_code}：{resp.text[:200]}")
            break

        goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
        if not goods:
            st.info(f"第 {page} 頁沒有資料，停止爬取。")
            break

        for g in goods:
            rows.append(
                {
                    "頁數": page,
                    "品名": g.get("goodsName", ""),
                    "價格": g.get("goodsPrice", ""),
                }
            )
        time.sleep(delay)  # 禮貌性延遲，避免被擋
    progress.progress(1.0, text="完成")

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 價格清理：去掉 $、逗號等非數字字元
    df["價格"] = df["價格"].astype(str).str.replace(r"[^\d]", "", regex=True)
    df = df[df["價格"] != ""].copy()
    df["價格"] = df["價格"].astype(int)
    df = df.drop_duplicates(subset=["品名", "價格"]).reset_index(drop=True)
    df["品牌"] = df["品名"].apply(extract_brand)
    return df


# ---------------------------------------------------------------
# 圖表
# ---------------------------------------------------------------
BINS = [0, 500, 1000, 1500, 2000, 3000, 5000, float("inf")]
LABELS = ["<500", "500-999", "1000-1499", "1500-1999", "2000-2999", "3000-4999", "5000+"]


def add_price_bucket(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["價格區間"] = pd.cut(out["價格"], bins=BINS, labels=LABELS, right=False).astype(str)
    return out


def line_chart(df: pd.DataFrame, keyword: str):
    mean_price = df["價格"].mean()
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["價格"],
            mode="lines+markers",
            name="價格",
            text=df["品名"],
            hovertemplate="#%{x}<br>%{text}<br>$%{y:,}<extra></extra>",
        )
    )
    fig.add_hline(
        y=mean_price,
        line_dash="dash",
        line_color="red",
        annotation_text=f"平均 {mean_price:,.0f}",
    )
    fig.update_layout(
        title=f"MOMO「{keyword}」售價折線圖",
        xaxis_title="商品序號",
        yaxis_title="價格 (NTD)",
        height=500,
    )
    return fig


def pie_chart(df: pd.DataFrame):
    counts = df["價格區間"].value_counts().reindex(LABELS).dropna().reset_index()
    counts.columns = ["價格區間", "商品數"]
    return px.pie(counts, names="價格區間", values="商品數", title="價格區間分布", hole=0.3)


def sunburst_chart(df: pd.DataFrame, top_n: int):
    d = df.copy()
    top_brands = d["品牌"].value_counts().head(top_n).index
    d["品牌"] = d["品牌"].where(d["品牌"].isin(top_brands), "其他")
    g = d.groupby(["價格區間", "品牌"]).size().reset_index(name="商品數")
    return px.sunburst(
        g,
        path=["價格區間", "品牌"],
        values="商品數",
        title=f"價格區間 → 品牌（前 {top_n} 大品牌，其餘歸為「其他」）",
    )


# ---------------------------------------------------------------
# UI
# ---------------------------------------------------------------
st.title("🛒 MOMO 商品價格分析")

with st.sidebar:
    st.header("搜尋條件")
    keyword = st.text_input("產品名稱", value="行動電源")
    pages = st.number_input("爬取頁數", min_value=1, max_value=20, value=2, step=1)
    delay = st.slider("每頁間隔秒數", 0.5, 5.0, 1.0, 0.5)
    top_n = st.slider("旭日圖顯示品牌數", 3, 20, 8)
    run = st.button("開始爬取", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.error("請輸入產品名稱")
    else:
        with st.spinner("爬取中…"):
            result = scrape(keyword.strip(), int(pages), delay)
        if result.empty:
            st.error("沒有取得任何資料，可能被擋或關鍵字無結果。")
            st.session_state.pop("data", None)
        else:
            st.session_state["data"] = result
            st.session_state["keyword"] = keyword.strip()

if "data" in st.session_state:
    df = add_price_bucket(st.session_state["data"])
    kw = st.session_state["keyword"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("商品數", f"{len(df):,}")
    c2.metric("平均價格", f"${df['價格'].mean():,.0f}")
    c3.metric("最高價格", f"${df['價格'].max():,}")
    c4.metric("最低價格", f"${df['價格'].min():,}")

    tab1, tab2, tab3, tab4 = st.tabs(["折線圖", "圓餅圖", "旭日圖", "原始資料"])
    with tab1:
        st.plotly_chart(line_chart(df, kw), use_container_width=True)
    with tab2:
        st.plotly_chart(pie_chart(df), use_container_width=True)
    with tab3:
        st.plotly_chart(sunburst_chart(df, top_n), use_container_width=True)
    with tab4:
        st.dataframe(df, use_container_width=True)
        st.download_button(
            "下載 CSV",
            data=df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name=f"MOMO_{kw}_{time.strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )
else:
    st.info("請在左側輸入產品名稱與頁數，然後按「開始爬取」。")
