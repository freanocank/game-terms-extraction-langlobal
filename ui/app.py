"""Streamlit UI for Game-Terms-Extraction pipeline."""

from __future__ import annotations

import sys
import json
import hashlib
import shutil
import tempfile
import datetime
from html import escape
from io import BytesIO
from pathlib import Path
import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.main import (
    MATCH_TYPE_SOURCE_LABELS,
    empty_glossary_bytes,
    descriptive_template_filename,
    results_workbook_bytes,
    template_workbook_bytes,
)
from ui.ui_backend import (
    RunConfig, default_config, ProcessingTask,
    get_all_models, add_model_and_select, remove_model,
    list_profiles, save_uploaded_profile, delete_profile,
    test_api_connection, get_profile_template, get_profile_content,
    save_persisted_config, check_checkpoint,
    reset_embed_db, embed_db_term_count,
    load_ckpt_meta, resolve_service_settings, SERVICE_LABELS,
    PERSISTED_CONFIG_FIELDS,
)

LOGO = str(ROOT / "assets" / "logo.jpeg")
st.set_page_config(page_title="游戏术语提取工具 — Langlobal", page_icon=LOGO, layout="wide")
st.logo(LOGO)

# Langlobal workbench theme
st.markdown(
    """
    <style>
    :root {
        --lg-ink: #172331;
        --lg-muted: #627083;
        --lg-line: #d9e2e8;
        --lg-paper: #f6f8fa;
        --lg-panel: #ffffff;
        --lg-green: #2f6f4e;
        --lg-green-soft: #e7f1ea;
        --lg-blue: #4f87a7;
        --lg-blush: #d99595;
    }
    html, body {
        font-family: "Avenir Next", "PingFang SC", "Microsoft YaHei", sans-serif;
        color: var(--lg-ink);
    }
    h1, h2, h3, h4 {
        scroll-margin-top: 5rem;
        text-wrap: balance;
    }
    [data-testid="stHeadingWithActionElements"] > h2:not(.lg-step) {
        font-size: clamp(1.45rem, 2.5vw, 1.75rem) !important;
        line-height: 1.3 !important;
    }
    [data-testid="stAppViewContainer"] {
        background:
            linear-gradient(90deg, rgba(79, 135, 167, 0.035) 1px, transparent 1px),
            linear-gradient(rgba(79, 135, 167, 0.035) 1px, transparent 1px),
            var(--lg-paper);
        background-size: 32px 32px;
    }
    [data-testid="stMainBlockContainer"] {
        max-width: 1180px;
        padding-top: 2rem;
        padding-bottom: 4rem;
    }
    section[data-testid="stSidebar"] {
        background: #162533;
        border-right: 1px solid #263949;
    }
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] h3 {
        color: #eaf0f3 !important;
    }
    section[data-testid="stSidebar"] [role="radiogroup"] label {
        padding: 0.45rem 0.6rem;
        border-radius: 8px;
        line-height: 1.35;
    }
    section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
        background: rgba(73, 139, 101, 0.2);
    }
    [data-testid="stSidebarLogo"],
    [data-testid="stLogo"],
    [data-testid="stHeaderLogo"] { border-radius: 12px !important; }
    section[data-testid="stSidebar"] [data-testid="stSidebarLogo"],
    section[data-testid="stSidebar"] [data-testid="stLogo"] {
        width: 64px !important;
        height: 64px !important;
        max-height: none !important;
        object-fit: cover;
        border: 2px solid rgba(255, 255, 255, 0.9);
        border-radius: 16px !important;
        box-shadow: 0 8px 20px rgba(0, 0, 0, 0.2);
    }
    section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {
        min-height: 80px;
        padding-top: 12px;
        align-items: flex-start;
    }
    section[data-testid="stSidebar"] [data-testid="stSidebarLogo"] {
        margin-top: 0 !important;
    }
    [data-testid="stHeaderLogo"] {
        width: 44px !important;
        height: 44px !important;
        object-fit: cover;
    }
    .lg-brand {
        margin: 0.2rem 0 1.15rem;
    }
    .lg-brand__name {
        color: #fff;
        font-size: 1.02rem;
        font-weight: 700;
        letter-spacing: 0.02em;
    }
    .lg-brand__meta {
        margin-top: 0.2rem;
        color: #9eb2bf;
        font-family: "SFMono-Regular", Consolas, monospace;
        font-size: 0.68rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    section[data-testid="stSidebar"] hr {
        margin: 1.2rem 0 1rem;
        border-color: #496071 !important;
    }
    .lg-sidebar-status {
        margin: 0 0 0.85rem;
        padding: 0.82rem 0.85rem 0.72rem;
        border: 1px solid #31495b;
        border-radius: 12px;
        background: #1b2d3b;
        box-shadow: 0 8px 22px rgba(5, 14, 21, 0.16);
    }
    .lg-sidebar-status__title,
    .lg-sidebar-status__section {
        color: #90aab9;
        font-family: "SFMono-Regular", Consolas, monospace;
        font-size: 0.64rem;
        font-weight: 700;
        letter-spacing: 0.11em;
        line-height: 1.4;
        text-transform: uppercase;
    }
    .lg-sidebar-status__title {
        margin-bottom: 0.42rem;
        color: #dbe7ed;
    }
    .lg-sidebar-status__section {
        margin: 0.62rem 0 0.25rem;
        padding-top: 0.56rem;
        border-top: 1px solid #31495b;
    }
    .lg-sidebar-status__row {
        display: grid;
        grid-template-columns: 4.2rem minmax(0, 1fr);
        gap: 0.55rem;
        align-items: start;
        min-width: 0;
        padding: 0.2rem 0;
    }
    .lg-sidebar-status__label {
        color: #aabac4;
        font-size: 0.72rem;
        line-height: 1.35;
        white-space: nowrap;
    }
    .lg-sidebar-status__value {
        display: -webkit-box;
        min-width: 0;
        overflow: hidden;
        color: #f1f6f8;
        font-family: "SFMono-Regular", Consolas, monospace;
        font-size: 0.7rem;
        font-weight: 650;
        font-variant-numeric: tabular-nums;
        line-height: 1.35;
        overflow-wrap: anywhere;
        text-align: right;
        text-overflow: ellipsis;
        white-space: normal;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 2;
    }
    .lg-hero {
        position: relative;
        overflow: hidden;
        margin: 0 0 1.65rem;
        padding: 1.5rem 1.6rem 1.35rem;
        border: 1px solid var(--lg-line);
        border-radius: 20px;
        background: rgba(255, 255, 255, 0.94);
        box-shadow: 0 14px 36px rgba(23, 35, 49, 0.06);
    }
    .lg-hero__kicker {
        color: var(--lg-green);
        font-family: "SFMono-Regular", Consolas, monospace;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    .lg-hero h1 {
        position: relative;
        z-index: 1;
        margin: 0.34rem 0 0.38rem;
        color: var(--lg-ink);
        font-family: "Avenir Next", "PingFang SC", sans-serif;
        font-size: clamp(1.8rem, 4vw, 2.65rem);
        font-weight: 750;
        letter-spacing: -0.035em;
        line-height: 1.08;
    }
    .lg-hero__copy {
        position: relative;
        z-index: 1;
        max-width: 760px;
        margin: 0;
        color: var(--lg-muted);
        font-size: 0.98rem;
        line-height: 1.7;
        overflow-wrap: anywhere;
    }
    .lg-pipeline {
        display: flex;
        align-items: center;
        gap: 0.45rem;
        margin-top: 1.1rem;
        color: #5c6b7a;
        font-size: 0.76rem;
        font-weight: 650;
    }
    .lg-pipeline__node {
        padding: 0.35rem 0.58rem;
        border: 1px solid #cfdae1;
        border-radius: 999px;
        background: #f8fafb;
    }
    .lg-pipeline__node:first-child {
        color: var(--lg-green);
        border-color: #9fc1ab;
        background: var(--lg-green-soft);
    }
    .lg-step {
        margin: 1.3rem 0 0.7rem;
        color: var(--lg-ink);
        font-size: 1.08rem !important;
        font-weight: 750;
        letter-spacing: -0.01em;
        line-height: 1.45 !important;
    }
    .lg-step .lg-step__number {
        display: inline-grid;
        width: 1.7rem;
        height: 1.7rem;
        margin-right: 0.5rem;
        place-items: center;
        border-radius: 7px;
        color: #fff;
        background: var(--lg-green);
        font-family: "SFMono-Regular", Consolas, monospace;
        font-size: 0.72rem;
    }
    [data-testid="stFileUploader"] {
        padding: 1rem 1rem 0.2rem;
        border: 1px solid var(--lg-line);
        border-radius: 16px;
        background: rgba(255, 255, 255, 0.9);
    }
    [data-testid="stFileUploaderDropzone"] {
        border: 1px dashed #9eb8aa;
        border-radius: 11px;
        background: #f4f8f5;
    }
    [data-testid="stFileUploaderDropzone"]
    [data-testid="stBaseButton-secondary"]
    [data-testid="stMarkdownContainer"] p {
        font-size: 0;
        visibility: hidden;
    }
    [data-testid="stFileUploaderDropzone"]
    [data-testid="stBaseButton-secondary"]
    [data-testid="stMarkdownContainer"] p::after {
        content: "选择文件";
        font-size: 0.875rem;
        visibility: visible;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] span {
        font-size: 0;
        visibility: hidden;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] span::after {
        content: "单个文件最大 200 MB";
        color: var(--lg-muted);
        font-size: 0.82rem;
        visibility: visible;
    }
    [data-testid="stMetric"] {
        min-height: 104px;
        padding: 0.9rem 1rem;
        border: 1px solid var(--lg-line);
        border-radius: 14px;
        background: rgba(255, 255, 255, 0.94);
        box-shadow: 0 8px 22px rgba(23, 35, 49, 0.045);
    }
    [data-testid="stMetricLabel"] { color: var(--lg-muted); }
    [data-testid="stMetricValue"] {
        color: var(--lg-ink);
        font-family: "Avenir Next", "PingFang SC", sans-serif;
        font-weight: 750;
        font-variant-numeric: tabular-nums;
    }
    [data-testid="stButton"] button,
    [data-testid="stDownloadButton"] button {
        min-height: 2.65rem;
        border-radius: 10px;
        font-weight: 650;
        touch-action: manipulation;
        transition: transform 140ms ease, box-shadow 140ms ease, border-color 140ms ease;
    }
    [data-testid="stButton"] button[kind="secondary"],
    [data-testid="stDownloadButton"] button[kind="secondary"] {
        color: var(--lg-ink);
        background: #ffffff;
        border-color: #71818d;
    }
    [data-testid="stButton"] button[kind="secondary"] p,
    [data-testid="stDownloadButton"] button[kind="secondary"] p {
        color: var(--lg-ink) !important;
    }
    [data-testid="stButton"] button[kind="primary"],
    [data-testid="stDownloadButton"] button[kind="primary"] {
        color: #ffffff;
        background: var(--lg-green);
        border-color: var(--lg-green);
        box-shadow: 0 8px 18px rgba(47, 111, 78, 0.2);
    }
    [data-testid="stButton"] button[kind="primary"] p,
    [data-testid="stDownloadButton"] button[kind="primary"] p {
        color: #ffffff !important;
    }
    [data-testid="stButton"] button:hover,
    [data-testid="stDownloadButton"] button:hover {
        transform: translateY(-1px);
        box-shadow: 0 7px 16px rgba(23, 35, 49, 0.09);
    }
    [data-testid="stButton"] button[kind="secondary"]:hover,
    [data-testid="stDownloadButton"] button[kind="secondary"]:hover {
        color: #101b26;
        background: #edf3ef;
        border-color: #71818d;
    }
    [data-testid="stButton"] button[kind="secondary"]:hover p,
    [data-testid="stDownloadButton"] button[kind="secondary"]:hover p {
        color: #101b26 !important;
    }
    [data-testid="stButton"] button[kind="primary"]:hover,
    [data-testid="stDownloadButton"] button[kind="primary"]:hover {
        color: #ffffff;
        background: #245c3f;
        border-color: #245c3f;
    }
    [data-testid="stButton"] button:active:not(:disabled),
    [data-testid="stDownloadButton"] button:active:not(:disabled) {
        transform: translateY(0);
        box-shadow: inset 0 2px 4px rgba(23, 35, 49, 0.16);
    }
    [data-testid="stButton"] button:focus-visible,
    [data-testid="stDownloadButton"] button:focus-visible,
    [data-testid="stFileUploaderDropzone"]:focus-visible,
    [data-testid="stFileUploaderDropzone"]:focus-within,
    section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:focus-visible),
    [data-testid="stCheckbox"] label:has(input:focus-visible) {
        outline: 3px solid #4f87a7;
        outline-offset: 2px;
        box-shadow: 0 0 0 4px rgba(79, 135, 167, 0.2);
    }
    [data-baseweb="input"]:focus-within,
    [data-baseweb="select"]:focus-within,
    [data-baseweb="textarea"]:focus-within {
        border-radius: 8px;
        box-shadow: 0 0 0 3px rgba(79, 135, 167, 0.35);
    }
    section[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] {
        color: #173d2b;
        background: #e7f1ea;
        border-color: #9fc1ab;
    }
    section[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] p {
        color: #173d2b !important;
    }
    section[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover {
        color: #102d20;
        background: #cfe4d5;
        border-color: #6f9b7d;
    }
    section[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover p {
        color: #102d20 !important;
    }
    [data-testid="stButton"] button:disabled,
    [data-testid="stDownloadButton"] button:disabled,
    section[data-testid="stSidebar"] [data-testid="stButton"] button:disabled {
        color: #52616d !important;
        background: #e2e8e5 !important;
        border-color: #a9b5af !important;
        opacity: 1 !important;
        box-shadow: none;
        transform: none;
    }
    [data-testid="stButton"] button:disabled p,
    [data-testid="stDownloadButton"] button:disabled p,
    section[data-testid="stSidebar"] [data-testid="stButton"] button:disabled p {
        color: #52616d !important;
    }
    [data-testid="stDataFrame"],
    [data-testid="stAlert"],
    [data-testid="stExpander"] {
        border-radius: 12px;
        overflow: hidden;
    }
    [data-testid="stMarkdownContainer"] code {
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
    }
    hr { border-color: var(--lg-line) !important; }
    @media (max-width: 720px) {
        [data-testid="stMainBlockContainer"] { padding-top: 3.25rem; }
        .lg-hero { padding: 1.2rem; border-radius: 16px; }
        .lg-pipeline { flex-wrap: wrap; }
        .lg-pipeline > span[aria-hidden="true"] { display: none; }
        [data-testid="stMetric"] { min-height: 88px; }
        [data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap;
            gap: 0.75rem;
        }
        [data-testid="stMainBlockContainer"] [data-testid="stColumn"] {
            flex: 1 1 100% !important;
            width: 100% !important;
            min-width: 0 !important;
        }
    }
    @media (prefers-reduced-motion: reduce) {
        [data-testid="stButton"] button,
        [data-testid="stDownloadButton"] button { transition: none; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _page_intro(kicker: str, title: str, copy: str, show_pipeline: bool = False):
    pipeline = """
        <div class="lg-pipeline" aria-label="术语处理流程">
            <span class="lg-pipeline__node">源文本</span><span aria-hidden="true">→</span>
            <span class="lg-pipeline__node">术语提取</span><span aria-hidden="true">→</span>
            <span class="lg-pipeline__node">译文生成</span><span aria-hidden="true">→</span>
            <span class="lg-pipeline__node">审核导出</span>
        </div>
    """ if show_pipeline else ""
    st.markdown(
        f"""
        <section class="lg-hero">
            <div class="lg-hero__kicker">{kicker}</div>
            <h1>{title}</h1>
            <p class="lg-hero__copy">{copy}</p>
            {pipeline}
        </section>
        """,
        unsafe_allow_html=True,
    )


def _step_heading(number: int, title: str):
    st.markdown(
        f'<h2 class="lg-step"><span class="lg-step__number">{number:02d}</span>{title}</h2>',
        unsafe_allow_html=True,
    )


def _run_blockers(source_ready: bool, cfg, task, needs_embedding: bool = False) -> list[str]:
    blockers = []
    if not source_ready:
        blockers.append("上传源文本文件")
    if not cfg.api_key:
        blockers.append("在设置中填写主生成服务 API 密钥")
    if not cfg.model:
        blockers.append("选择 AI 模型")
    if not cfg.profile:
        blockers.append("选择项目提取规则")
    if not resolve_service_settings(cfg, "ner").api_key:
        blockers.append("填写人名/地名预识别（NER）服务 API 密钥，或让其继承主服务")
    if needs_embedding and not resolve_service_settings(cfg, "embedding").api_key:
        blockers.append("填写术语相似度匹配（Embedding）服务 API 密钥，或让其继承主服务")
    if task is not None and task.is_running:
        blockers.append("等待当前任务结束")
    return blockers

# ═══════════════════════════════════════════════════════════
# Session state init
# ═══════════════════════════════════════════════════════════

DEFAULTS = {
    "cfg": None,
    "task": None,
    "task_results": None,
    "sel_model": "",
    "sel_profile": "",
    "api_test_result": None,
}

for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.cfg is None:
    cfg = default_config()
    models = get_all_models()
    profiles = list_profiles()
    if cfg.model not in models:
        cfg.model = models[0] if models else ""
    if cfg.profile not in profiles:
        cfg.profile = profiles[0] if profiles else ""
    st.session_state.cfg = cfg

# Resolve pending widget changes from previous handler runs (before any widget renders)
for _key in ("sel_model", "sel_profile", "_src_col", "_gl_cn_col", "_gl_en_col", "_key_col", "_src_en_col", "_bilingual"):
    _pending = f"_pending_{_key}"
    if _pending in st.session_state:
        st.session_state[_key] = st.session_state.pop(_pending)


def _ensure_selection(key: str, cfg_attr: str, options: list[str]):
    cfg = st.session_state.cfg
    current = st.session_state.get(key, "")
    cfg_value = getattr(cfg, cfg_attr, "")
    if options:
        if current not in options:
            current = cfg_value if cfg_value in options else options[0]
            st.session_state[key] = current
        setattr(cfg, cfg_attr, current)
    elif not current and cfg_value:
        st.session_state[key] = cfg_value


def _ensure_widget_choice(key: str, options, fallback):
    choices = list(options)
    if not choices:
        return choices
    if st.session_state.get(key) not in choices:
        st.session_state[key] = fallback if fallback in choices else choices[0]
    return choices


def _ensure_widget_bool(key: str, fallback: bool):
    if key not in st.session_state:
        st.session_state[key] = bool(fallback)


def _retry_header_detection():
    st.session_state._retry_manual_values = {
        key: st.session_state[key]
        for key in ("_src_col", "_key_col", "_src_en_col", "_bilingual", "_gl_cn_col", "_gl_en_col")
        if key in st.session_state
    }
    st.session_state._retry_col_detection = True
    st.session_state.pop("_col_detected", None)


_ensure_selection("sel_model", "model", get_all_models())
_ensure_selection("sel_profile", "profile", list_profiles())


def _sync_cfg():
    cfg = st.session_state.cfg
    m = st.session_state.get("sel_model", "")
    p = st.session_state.get("sel_profile", "")
    if m:
        cfg.model = m
    if p:
        cfg.profile = p


def _service_signature(cfg: RunConfig, service: str) -> str:
    effective = resolve_service_settings(cfg, service)
    payload = "\0".join((effective.api_key or "", effective.api_base or "", effective.model or ""))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _show_api_test_result(state_key: str, signature: str):
    result = st.session_state.get(state_key)
    if not isinstance(result, dict) or result.get("signature") != signature:
        st.session_state.pop(state_key, None)
        return
    (st.success if result.get("ok") else st.error)(result.get("message", "连接测试未返回结果。"))


def _render_aux_service_editor(cfg: RunConfig, service: str, title: str,
                               description: str, model_label: str, env_hint: str):
    st.markdown(f"### {title}")
    st.caption(description)
    use_attr = f"{service}_use_main"
    use_main = st.checkbox(
        "密钥和地址全部沿用主生成服务",
        value=getattr(cfg, use_attr),
        key=f"{service}_use_main_widget",
    )
    setattr(cfg, use_attr, use_main)
    if not use_main:
        col_key, col_base = st.columns(2)
        with col_key:
            setattr(
                cfg,
                f"{service}_api_key",
                st.text_input(
                    f"{title} API 密钥",
                    value=getattr(cfg, f"{service}_api_key"),
                    type="password",
                    key=f"{service}_api_key_widget",
                    help=f"留空时继续沿用主服务密钥；也可使用环境变量 {env_hint}。",
                ),
            )
        with col_base:
            setattr(
                cfg,
                f"{service}_api_base",
                st.text_input(
                    f"{title}服务地址（Base URL）",
                    value=getattr(cfg, f"{service}_api_base"),
                    key=f"{service}_api_base_widget",
                ),
            )
    setattr(
        cfg,
        f"{service}_model",
        st.text_input(
            model_label,
            value=getattr(cfg, f"{service}_model"),
            key=f"{service}_model_widget",
        ),
    )
    effective = resolve_service_settings(cfg, service)
    source = "密钥与地址继承主服务；模型单独配置" if use_main else "使用自定义项（空项仍继承主服务）"
    st.caption(f"当前：{source} · 模型 `{effective.model or '未设置'}`")
    signature = _service_signature(cfg, service)
    result_key = f"api_test_result_{service}"
    if st.button(f"测试{title}连接", key=f"test_{service}_service"):
        with st.spinner(f"正在测试{title}…"):
            ok, message = test_api_connection(cfg, service)
            st.session_state[result_key] = {
                "signature": signature,
                "ok": ok,
                "message": message,
            }
    _show_api_test_result(result_key, signature)


# ═══════════════════════════════════════════════════════════
# Page router
# ═══════════════════════════════════════════════════════════

PAGES = {"提取术语": "process", "结果与导出": "results", "设置": "settings"}

with st.sidebar:
    st.markdown(
        """
        <div class="lg-brand">
            <div class="lg-brand__name">游戏术语工作台</div>
            <div class="lg-brand__meta">Langlobal · Localization AI</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    page = st.radio("功能导航", list(PAGES.keys()))

page_id = PAGES[page]

# ═══════════════════════════════════════════════════════════
# Page: Settings
# ═══════════════════════════════════════════════════════════

if page_id == "settings":
    _page_intro(
        "WORKBENCH SETTINGS",
        "设置",
        "分别配置主生成、人名/地名预识别、术语相似度匹配与表头识别服务。密钥只在当前进程内存中使用，不写入项目文件。",
    )

    settings_notice = st.session_state.pop("_settings_notice", None)
    if settings_notice:
        st.success(settings_notice)

    cfg = st.session_state.cfg

    # ── API ──────────────────────────────────────────
    st.header("主生成服务")
    st.caption("用于术语提取和新术语翻译；其他 3 项服务可继承此密钥和地址。")
    col1, col2 = st.columns(2)
    with col1:
        cfg.api_key = st.text_input("API 密钥", value=cfg.api_key, type="password",
                                    help="仅保存在当前进程内存；也可通过 OPENAI_API_KEY 提供。")
    with col2:
        cfg.api_base = st.text_input("API 服务地址（Base URL）", value=cfg.api_base,
                                     help="兼容 OpenAI 接口的服务根地址，例如 https://api.openai.com/v1。")

    # ── Model ────────────────────────────────────────
    st.header("AI 模型")
    all_models = get_all_models()

    col_m1, col_m2, col_m3 = st.columns([3, 2, 1.35])
    with col_m1:
        if all_models:
            selected_model = st.selectbox(
                "当前使用的模型",
                all_models,
                index=all_models.index(cfg.model) if cfg.model in all_models else 0,
            )
        else:
            selected_model = st.text_input("模型 ID", value=cfg.model)
        cfg.model = selected_model
        st.session_state.sel_model = selected_model

    with col_m2:
        new_model = st.text_input("自定义模型 ID", placeholder="例如：gpt-4o")
        model_name = new_model.strip()
        if st.button("添加并选用", width="stretch", disabled=not model_name,
                     help="输入供应商提供的模型 ID" if not model_name else None):
            existed = model_name in all_models
            added_model = add_model_and_select(cfg, new_model)
            if added_model:
                st.session_state._pending_sel_model = added_model
                st.session_state._settings_notice = (
                    f"已选用现有模型“{added_model}”。" if existed
                    else f"已添加并选用模型“{added_model}”。"
                )
                st.rerun()

    with col_m3:
        delete_disabled = len(all_models) <= 1
        st.write("")
        with st.popover(
            "移除当前模型",
            width="stretch",
            disabled=delete_disabled,
            help="至少保留 1 个模型" if delete_disabled else "从本机模型列表中移除当前选中的模型",
        ):
            current_model = st.session_state.sel_model
            st.warning(f"确认移除模型“{current_model}”？之后仍可重新添加。")
            if st.button("确认移除模型", key="confirm_remove_model", type="primary", width="stretch"):
                remove_model(current_model)
                remaining = get_all_models()
                st.session_state._pending_sel_model = remaining[0] if remaining else ""
                st.session_state._settings_notice = f"已移除模型“{current_model}”。"
                _sync_cfg()
                st.rerun()

    with st.expander("辅助 API 服务（可分别配置）", expanded=False):
        st.info(
            "默认沿用主生成服务的密钥和地址。若不同能力由不同供应商提供，请关闭对应的继承选项并填写独立配置。"
        )
        _render_aux_service_editor(
            cfg,
            "ner",
            "人名/地名预识别（NER）",
            "在正式提取前识别人名和地名，减少漏提。",
            "NER 模型 ID",
            "NER_API_KEY",
        )
        st.divider()
        _render_aux_service_editor(
            cfg,
            "embedding",
            "术语相似度匹配（Embedding）",
            "对参考术语编码，用于给新译文查找最接近的项目术语。",
            "Embedding 模型 ID",
            "EMBEDDING_API_KEY",
        )
        st.divider()
        _render_aux_service_editor(
            cfg,
            "detect",
            "表头识别",
            "上传 Excel 后自动判断原文、Key、译文和术语列；不可用时仍可手动选择。",
            "表头识别模型 ID",
            "DETECT_API_KEY",
        )

    # ── Advanced Settings ────────────────────────────
    with st.expander("项目规则与性能", expanded=False):
        st.header("项目提取规则")
        st.caption("规则决定术语分类、提取边界和翻译风格；每个游戏项目可使用一份独立 YAML 配置。")

        profiles = list_profiles()

        col_p1, col_p2 = st.columns(2)
        with col_p1:
            if profiles:
                selected_profile = st.selectbox(
                    "当前项目规则",
                    profiles,
                    index=profiles.index(cfg.profile) if cfg.profile in profiles else 0,
                    help="选择本次提取使用的项目 YAML 规则",
                )
            else:
                selected_profile = st.text_input("项目规则名称", value=cfg.profile)
            cfg.profile = selected_profile
            st.session_state.sel_profile = selected_profile

        with col_p2:
            uploaded_yaml = st.file_uploader(
                "导入项目规则（YAML）", type=["yaml", "yml"],
                help="导入后会保存到 profiles/，并自动成为当前项目规则。",
            )
            if uploaded_yaml is not None:
                try:
                    name = save_uploaded_profile(uploaded_yaml.name, uploaded_yaml.getvalue())
                    st.session_state._pending_sel_profile = name
                    st.session_state._settings_notice = f"已导入并选中项目规则“{name}”。"
                    _sync_cfg()
                    st.rerun()
                except Exception as e:
                    st.error(f"YAML 格式错误：{e}。请按配置模板修正后重试。")

        if st.session_state.sel_profile:
            content = get_profile_content(st.session_state.sel_profile)
            col_d1, col_d2, _ = st.columns([1, 1, 4])
            with col_d1:
                if content:
                    st.download_button(
                        f"下载 {st.session_state.sel_profile}.yaml",
                        data=content,
                        file_name=f"{st.session_state.sel_profile}.yaml",
                        mime="text/yaml",
                        width="stretch",
                    )
            with col_d2:
                disabled = len(profiles) <= 1
                with st.popover(
                    "删除项目规则",
                    width="stretch",
                    disabled=disabled,
                    help="至少保留 1 份项目规则" if disabled else "永久删除当前项目规则文件",
                ):
                    current_profile = st.session_state.sel_profile
                    st.warning(f"确认永久删除项目规则“{current_profile}”？此操作无法撤销。")
                    if st.button("确认删除项目规则", key="confirm_delete_profile",
                                 type="primary", width="stretch"):
                        delete_profile(current_profile)
                        remaining = list_profiles()
                        st.session_state._pending_sel_profile = remaining[0] if remaining else ""
                        st.session_state._settings_notice = f"已删除项目规则“{current_profile}”。"
                        _sync_cfg()
                        st.rerun()

        st.divider()
        template = get_profile_template()
        if template:
            st.download_button(
                "下载项目规则模板（YAML）",
                data=template,
                file_name="profile_template.yaml",
                mime="text/yaml",
                help="下载一份空白配置模板，供 PM 填写后上传",
            )


        st.divider()
        st.header("术语匹配数据")
        db_count = embed_db_term_count()
        if db_count > 0:
            st.caption(f"当前已缓存 {db_count} 条参考术语的匹配数据")
            st.info(
                "**正常情况下无需手动重置。** 每次运行时，新增的术语会自动加入向量库。\n\n"
                "仅在以下情况点击重置：\n"
                "- 切换了术语相似度匹配（Embedding）模型（切换后旧向量无效）\n"
                "- 术语表大幅缩减、需要清除旧向量\n"
                "- 向量库出现异常"
            )
            with st.popover("清除术语匹配数据", help="下次使用参考术语表时自动重建"):
                st.warning(
                    f"确认清除当前 {db_count} 条术语的匹配数据？"
                    "下次使用参考术语表运行任务时会自动重建。"
                )
                if st.button("确认清除匹配数据", key="confirm_reset_embed_db",
                             type="primary", width="stretch"):
                    reset_embed_db()
                    st.session_state._settings_notice = "旧匹配数据已清除；下次使用参考术语表时会自动重建。"
                    st.rerun()
        else:
            st.caption("术语匹配数据尚未建立，首次使用参考术语表时会自动创建。")

        st.divider()

        cfg.max_concurrent = st.slider("AI 提取并发数", 1, 100, cfg.max_concurrent,
                                       help="同时发起的术语提取请求数；过高可能触发服务限流。")
        cfg.embed_workers = st.slider("向量编码并发数", 1, 100, cfg.embed_workers,
                                      help="同时处理参考术语向量的请求数；通常无需调整。")
        cfg.max_tokens = st.number_input(
            "单次响应 Token 上限",
            min_value=1024,
            max_value=262144,
            value=cfg.max_tokens,
            step=1024,
            help="单次 AI 响应允许的最大 Token 数；通常保持默认值。",
        )
        cfg.flag_unsourced = st.checkbox(
            "标记无法在源文本中定位的术语",
            value=cfg.flag_unsourced,
            help="开启后，这类术语会保留并标记为待复核，不会直接删除。",
        )


    st.divider()
    st.caption(
        "服务地址、模型与项目规则会保存到本机；所有 API 密钥仅保留在当前进程内存，重启后从环境变量重新读取。"
    )
    cfg_sig = tuple(getattr(cfg, name) for name in PERSISTED_CONFIG_FIELDS)
    if st.session_state.get("_saved_cfg") != cfg_sig:
        save_persisted_config(cfg)
        st.session_state._saved_cfg = cfg_sig

    detect_service = resolve_service_settings(cfg, "detect")
    detect_sig = (detect_service.api_key, detect_service.api_base, detect_service.model)
    if st.session_state.get("_detect_service_sig") not in (None, detect_sig):
        st.session_state.pop("_col_detected", None)
    st.session_state._detect_service_sig = detect_sig

# ═══════════════════════════════════════════════════════════
# Page: Process
# ═══════════════════════════════════════════════════════════

elif page_id == "process":
    _page_intro(
        "TERM EXTRACTION",
        "提取游戏术语",
        "上传源文本，确认需要分析的列，然后开始提取。已有双语术语表可选，用于沿用项目的既有译法。",
        show_pipeline=True,
    )

    process_notice = st.session_state.pop("_process_notice", None)
    if process_notice:
        st.success(process_notice)

    cfg = st.session_state.cfg
    _sync_cfg()

    _step_heading(1, "上传输入文件")
    col1, col2 = st.columns(2)
    with col1:
        src_file = st.file_uploader(
            "源文本文件（必填，.xlsx）", type=["xlsx"], key="src_upload",
            help="上传包含待提取中文文本的 Excel 文件。首行应为列名。",
            on_change=lambda: st.session_state.pop("_col_detected", None),
        )
    with col2:
        gl_file = st.file_uploader(
            "已有双语术语表（可选，.xlsx）", type=["xlsx"], key="gl_upload",
            help="用于沿用已有中英译法。不上传也可提取；新术语会由 AI 翻译。",
            on_change=lambda: st.session_state.pop("_col_detected", None),
        )

    # Cache uploads so files survive page navigation
    if src_file is not None:
        st.session_state["_src_cache"] = (src_file.name, src_file.getvalue())
    if gl_file is not None:
        st.session_state["_gl_cache"] = (gl_file.name, gl_file.getvalue())

    _src_cache = st.session_state.get("_src_cache")
    _gl_cache = st.session_state.get("_gl_cache")

    if src_file is None and _src_cache:
        c1, c2 = st.columns([6, 1])
        c1.caption(f"已选择源文本：`{_src_cache[0]}`")
        if c2.button("移除文件", key="clr_src"):
            del st.session_state["_src_cache"]
            st.session_state.pop("_col_detected", None)
            st.rerun()
    if gl_file is None and _gl_cache:
        c1, c2 = st.columns([6, 1])
        c1.caption(f"已选择参考术语表：`{_gl_cache[0]}`")
        if c2.button("移除文件", key="clr_gl"):
            del st.session_state["_gl_cache"]
            st.session_state.pop("_col_detected", None)
            st.rerun()

    eff_src = src_file.getvalue() if src_file else (_src_cache[1] if _src_cache else None)
    eff_src_name = src_file.name if src_file else (_src_cache[0] if _src_cache else "")
    eff_gl = gl_file.getvalue() if gl_file else (_gl_cache[1] if _gl_cache else None)
    eff_gl_name = gl_file.name if gl_file else (_gl_cache[0] if _gl_cache else "")

    if not eff_src and not eff_gl:
        st.session_state.pop("_col_info", None)
        st.session_state.pop("_col_errors", None)

    # ── Column detection (auto on upload) ──────────
    src_col = st.session_state.get("_src_col", 0)
    gl_cn_col = st.session_state.get("_gl_cn_col", 0)
    gl_en_col = st.session_state.get("_gl_en_col", 1)
    src_en_col = st.session_state.get("_src_en_col", 1)
    key_col = st.session_state.get("_key_col", -1)

    if (eff_src or eff_gl) and not st.session_state.get("_col_detected"):
        from core.header_detect import detect_source_column, detect_glossary_columns
        detect_service = resolve_service_settings(cfg, "detect")
        detected_info = {}
        detection_errors = []
        preserve_manual_on_failure = bool(st.session_state.pop("_retry_col_detection", False))
        retry_manual_values = st.session_state.pop("_retry_manual_values", {})
        if eff_src:
            try:
                df_src = pd.read_excel(BytesIO(eff_src))
                if df_src.shape[1] == 0:
                    raise ValueError("未找到任何列")
                d_src = detect_source_column(
                    df_src,
                    api_key=detect_service.api_key,
                    base_url=detect_service.api_base,
                    model=detect_service.model,
                )
                if d_src.get("method") == "default" and preserve_manual_on_failure:
                    for key in ("_src_col", "_key_col", "_src_en_col", "_bilingual"):
                        if key in retry_manual_values:
                            st.session_state[f"_pending_{key}"] = retry_manual_values[key]
                    manual_key = retry_manual_values.get("_key_col", -1)
                    manual_bilingual = bool(retry_manual_values.get("_bilingual", False))
                    d_src = {
                        **d_src,
                        "text_col": retry_manual_values.get("_src_col", d_src["text_col"]),
                        "key_col": None if manual_key is None or manual_key < 0 else manual_key,
                        "en_col": retry_manual_values.get("_src_en_col") if manual_bilingual else None,
                        "manual_preserved": True,
                    }
                else:
                    st.session_state._pending__src_col = d_src["text_col"]
                    st.session_state._pending__key_col = d_src["key_col"] if d_src.get("key_col") is not None else -1
                    st.session_state._pending__bilingual = d_src.get("en_col") is not None
                    if d_src.get("en_col") is not None:
                        st.session_state._pending__src_en_col = d_src["en_col"]
                detected_info.update({
                    "src": d_src,
                    "src_headers": [str(c) for c in df_src.columns],
                })
            except Exception as e:
                detection_errors.append(f"源文本文件无法读取表头：{e}")
        if eff_gl:
            try:
                df_gl = pd.read_excel(BytesIO(eff_gl))
                if df_gl.shape[1] < 2:
                    raise ValueError("至少需要术语原文和术语译文两列")
                d_gl = detect_glossary_columns(
                    df_gl,
                    api_key=detect_service.api_key,
                    base_url=detect_service.api_base,
                    model=detect_service.model,
                )
                if d_gl.get("method") == "default" and preserve_manual_on_failure:
                    for key in ("_gl_cn_col", "_gl_en_col"):
                        if key in retry_manual_values:
                            st.session_state[f"_pending_{key}"] = retry_manual_values[key]
                    d_gl = {
                        **d_gl,
                        "cn_col": retry_manual_values.get("_gl_cn_col", d_gl["cn_col"]),
                        "en_col": retry_manual_values.get("_gl_en_col", d_gl["en_col"]),
                        "manual_preserved": True,
                    }
                else:
                    st.session_state._pending__gl_cn_col = d_gl["cn_col"]
                    st.session_state._pending__gl_en_col = d_gl["en_col"]
                detected_info.update({
                    "gl": d_gl,
                    "gl_headers": [str(c) for c in df_gl.columns],
                })
            except Exception as e:
                detection_errors.append(f"参考术语表无法读取表头：{e}")
        st.session_state._col_info = detected_info
        st.session_state._col_errors = detection_errors
        st.session_state._col_detected = True
        st.rerun()

    col_info = st.session_state.get("_col_info", {})
    for detection_error in st.session_state.get("_col_errors", []):
        st.error(f"{detection_error}。请确认文件为有效 .xlsx，且首行包含列名。")
    mapping_cache_warning = st.session_state.pop("_mapping_cache_warning", None)
    if mapping_cache_warning:
        st.warning(mapping_cache_warning)

    _step_heading(2, "确认列映射与处理选项")
    if not col_info:
        st.caption("上传有效的源文本后，这里会显示自动识别的列映射和处理选项。")

    if col_info:
        src = col_info.get("src", {})
        gl = col_info.get("gl", {})

        def _method_label(result):
            if result.get("method") == "ai":
                return "本次 AI 自动识别"
            if result.get("method") == "default":
                return (
                    "AI 重试未完成，已保留手动选择"
                    if result.get("manual_preserved")
                    else "AI 表头识别未成功，已暂用默认列"
                )
            if result.get("method") == "cache":
                return {
                    "user": "人工确认记录",
                    "seed": "内置表头规则",
                    "ai": "AI 识别缓存",
                }.get(result.get("cache_source"), "历史列映射（需确认）")
            return "未知方式（需确认）"

        summaries = []
        if src:
            summaries.append(f"源文本列依据：{_method_label(src)}")
        if gl:
            summaries.append(f"参考术语列依据：{_method_label(gl)}")
        st.caption("　·　".join(summaries))

        fallback_items = []
        fallback_codes = []
        for label, result in (("源文本", src), ("参考术语表", gl)):
            if result.get("method") == "default":
                fallback_codes.append(result.get("fallback_code", "unknown"))
                preserved = "；已保留你的手动选择" if result.get("manual_preserved") else ""
                fallback_items.append(
                    f"- **{label}**：{result.get('fallback_reason') or 'AI 未返回有效的列映射'}{preserved}"
                )
        if fallback_items:
            message = (
                "AI 表头识别未完成；系统最初按默认规则选列。请核对下方当前选择，"
                "手动修改会保留且仍可继续提取。\n\n"
                + "\n".join(fallback_items)
            )
            st.warning(message)
            if set(fallback_codes) == {"missing_api_key"}:
                st.caption("请先在“设置”中配置表头识别密钥；返回本页后会自动重新识别。")
            else:
                st.button(
                    "重新尝试 AI 表头识别",
                    key="retry_header_detection",
                    on_click=_retry_header_detection,
                )

        if eff_src:
            src_headers = col_info.get("src_headers") or [str(c) for c in pd.read_excel(BytesIO(eff_src)).columns]
            src_options = _ensure_widget_choice(
                "_src_col", range(len(src_headers)), min(src.get("text_col", 0), len(src_headers) - 1)
            )
            st.selectbox("待分析的中文源文本列", src_options,
                         format_func=lambda i: f"[{i}] {src_headers[i]}",
                         key="_src_col",
                         help="AI 会从这一列的文本中识别术语。")
            key_options = _ensure_widget_choice(
                "_key_col",
                range(-1, len(src_headers)),
                src.get("key_col") if src.get("key_col") is not None else -1,
            )
            st.selectbox("文本 ID 列（可选）", key_options,
                         format_func=lambda i: "（未设置）" if i == -1 else f"[{i}] {src_headers[i]}",
                         key="_key_col",
                         help="用于保留每条源文本的唯一标识，并写入导出的标注模板 Key 值。")
            _ensure_widget_bool("_bilingual", cfg.bilingual)
            cfg.bilingual = st.checkbox(
                "使用源文件中的现有英文译文",
                key="_bilingual",
                help="适用于中英双语源文件：直接沿用对应英文列，不重复翻译。")
            if cfg.bilingual:
                src_en_options = _ensure_widget_choice(
                    "_src_en_col",
                    range(len(src_headers)),
                    min(src.get("en_col") if src.get("en_col") is not None else 1, len(src_headers) - 1),
                )
                st.selectbox("对应的英文译文列", src_en_options,
                             format_func=lambda i: f"[{i}] {src_headers[i]}",
                             key="_src_en_col")
            _ensure_widget_bool("_no_translate", cfg.no_translate)
            cfg.no_translate = st.checkbox(
                "仅提取术语，不生成新译文",
                key="_no_translate",
                help="结果中保留术语与分类；未提供的英文译文将留空。")
        if eff_gl:
            gl_headers = col_info.get("gl_headers") or [str(c) for c in pd.read_excel(BytesIO(eff_gl)).columns]
            gl_cn_options = _ensure_widget_choice(
                "_gl_cn_col", range(len(gl_headers)), min(gl.get("cn_col", 0), len(gl_headers) - 1)
            )
            gl_en_options = _ensure_widget_choice(
                "_gl_en_col", range(len(gl_headers)), min(gl.get("en_col", 1), len(gl_headers) - 1)
            )
            c1, c2 = st.columns(2)
            with c1:
                st.selectbox("术语原文列", gl_cn_options,
                             format_func=lambda i: f"[{i}] {gl_headers[i]}",
                             key="_gl_cn_col")
            with c2:
                st.selectbox("术语译文列", gl_en_options,
                             format_func=lambda i: f"[{i}] {gl_headers[i]}",
                             key="_gl_en_col")

        src_col = st.session_state.get("_src_col", 0)
        gl_cn_col = st.session_state.get("_gl_cn_col", 0)
        gl_en_col = st.session_state.get("_gl_en_col", 1)
        src_en_col = st.session_state.get("_src_en_col", 1)
        key_col = st.session_state.get("_key_col", -1)

    # ── Checkpoint status ──────────────────────
    ckpt_root = ROOT / "output" / "_checkpoints"
    all_ckpts = []
    if ckpt_root.exists():
        for d in sorted(ckpt_root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            ckpt_file = d / "checkpoint.json"
            if d.is_dir() and ckpt_file.exists():
                try:
                    data = json.loads(ckpt_file.read_text(encoding="utf-8"))
                    if data.get("chunk_idx", 0) > 0:
                        meta = load_ckpt_meta(str(d))
                        all_ckpts.append({
                            "name": d.name,
                            "chunk_idx": data["chunk_idx"],
                            "total": data.get("total_chunks", 0),
                            "terms": len(data.get("terms", [])),
                            "src_col": meta.get("src_col", 0),
                            "key_col": meta.get("key_col"),
                            "gl_cn_col": meta.get("gl_cn_col", 0),
                            "gl_en_col": meta.get("gl_en_col", 1),
                            "bilingual": meta.get("bilingual", False),
                            "src_en_col": meta.get("src_en_col", 1),
                            "no_translate": meta.get("no_translate", False),
                            "profile": meta.get("profile") or d.name.removeprefix("src_").rsplit("_", 1)[0],
                            "model": meta.get("model", ""),
                            "ner_use_main": meta.get("ner_use_main"),
                            "ner_api_base": meta.get("ner_base_url", ""),
                            "ner_model": meta.get("ner_model", ""),
                            "embedding_use_main": meta.get("embedding_use_main"),
                            "embedding_api_base": meta.get("embedding_base_url", ""),
                            "embedding_model": meta.get("embedding_model", ""),
                            "src_filename": meta.get("src_filename", ""),
                            "gl_filename": meta.get("gl_filename", ""),
                            "mtime": d.stat().st_mtime,
                        })
                except Exception:
                    pass

    # Auto-save uploaded files to matching checkpoint
    if eff_src and eff_gl and ckpt_root.exists():
        h = hashlib.md5(eff_src).hexdigest()[:8]
        for d in ckpt_root.iterdir():
            if d.is_dir() and h in d.name and cfg.profile in d.name:
                sp = d / "source.xlsx"
                gp = d / "glossary.xlsx"
                if not sp.exists():
                    sp.write_bytes(eff_src)
                if not gp.exists():
                    gp.write_bytes(eff_gl)
                break

    # Per-file match
    if eff_src:
        ckpt = check_checkpoint(eff_src, cfg.profile)
        if ckpt["exists"]:
            st.success(
                f"发现该文件的可恢复任务：已处理 {ckpt['chunk_idx']}/{ckpt['total_chunks']} 个文本批次，"
                f"已提取 {ckpt['terms']} 条术语。"
            )

    if all_ckpts:
        with st.expander(f"可恢复的未完成任务（{len(all_ckpts)} 个）", expanded=False):
            checkpoint_page_size = 10
            checkpoint_pages = (len(all_ckpts) + checkpoint_page_size - 1) // checkpoint_page_size
            checkpoint_page = 1
            if checkpoint_pages > 1:
                checkpoint_page_options = _ensure_widget_choice(
                    "_checkpoint_page", range(1, checkpoint_pages + 1), 1
                )
                checkpoint_page = st.selectbox(
                    "任务列表页",
                    checkpoint_page_options,
                    key="_checkpoint_page",
                    format_func=lambda value: f"第 {value}/{checkpoint_pages} 页",
                )
            checkpoint_start = (checkpoint_page - 1) * checkpoint_page_size
            visible_checkpoints = all_ckpts[checkpoint_start:checkpoint_start + checkpoint_page_size]
            for c in visible_checkpoints:
                src_path = ckpt_root / c["name"] / "source.xlsx"
                gl_path = ckpt_root / c["name"] / "glossary.xlsx"
                has_files = src_path.exists() and gl_path.exists()
                col_c1, col_c2, col_c3 = st.columns([3, 1, 1])
                with col_c1:
                    extra = "" if has_files else "（缺少原始文件）"
                    fn_info = f"  `{c['src_filename']}`" if c.get("src_filename") else ""
                    ts = datetime.datetime.fromtimestamp(c.get("mtime", 0)).strftime("%m-%d %H:%M")
                    st.caption(f"{ts} · {c['chunk_idx']}/{c['total']} 个批次 · {c['terms']} 条术语{fn_info}{extra}")
                with col_c2:
                    if st.button("继续任务", key=f"resume_{c['name']}", disabled=not has_files, width="stretch"):
                        cfg.profile = c.get("profile") or cfg.profile
                        cfg.model = c.get("model") or cfg.model
                        st.session_state._pending_sel_profile = cfg.profile
                        st.session_state._pending_sel_model = cfg.model
                        cfg.bilingual = c.get("bilingual", False)
                        cfg.no_translate = c.get("no_translate", False)
                        if c.get("ner_use_main") is not None:
                            cfg.ner_use_main = bool(c["ner_use_main"])
                        cfg.ner_api_base = c.get("ner_api_base") or cfg.ner_api_base
                        cfg.ner_model = c.get("ner_model") or cfg.ner_model
                        if c.get("embedding_use_main") is not None:
                            cfg.embedding_use_main = bool(c["embedding_use_main"])
                        cfg.embedding_api_base = c.get("embedding_api_base") or cfg.embedding_api_base
                        cfg.embedding_model = c.get("embedding_model") or cfg.embedding_model
                        missing = []
                        for service in ("main", "ner", "embedding"):
                            if not resolve_service_settings(cfg, service).api_key:
                                missing.append(SERVICE_LABELS[service])
                        if missing:
                            st.error("无法继续：请先在设置中填写 " + "、".join(missing) + "的 API 密钥。")
                        else:
                            task = ProcessingTask()
                            task.start(str(src_path), str(gl_path), cfg,
                                       src_col=c.get("src_col", 0),
                                       gl_cn_col=c.get("gl_cn_col", 0),
                                       gl_en_col=c.get("gl_en_col", 1),
                                       src_en_col=c.get("src_en_col", 1),
                                       src_bytes=src_path.read_bytes(),
                                       key_col=c.get("key_col"),
                                       checkpoint_dir=str(ckpt_root / c["name"]))
                            st.session_state.task = task
                            st.session_state.task_results = None
                            st.session_state._results_saved = False
                            st.rerun()
                with col_c3:
                    with st.popover("删除此任务", width="stretch", key=f"del_ckpt_{c['name']}"):
                        task_label = c.get("src_filename") or c["name"]
                        st.warning(f"确认永久删除未完成任务“{task_label}”？检查点与已提取进度都将删除。")
                        if st.button(
                            "确认删除未完成任务",
                            key=f"confirm_del_ckpt_{c['name']}",
                            type="primary",
                            width="stretch",
                        ):
                            shutil.rmtree(ckpt_root / c["name"])
                            st.session_state._process_notice = f"已删除未完成任务“{task_label}”。"
                            st.rerun()

    if eff_src and cfg.profile:
        reference_status = f"已加载 `{eff_gl_name}`" if eff_gl else "未提供，将直接生成新术语译文"
        st.caption(
            f"项目规则：`{cfg.profile}`　·　AI 模型：`{cfg.model}`　·　"
            f"参考术语：{reference_status}　·　提取并发：{cfg.max_concurrent}"
        )

    st.divider()

    _step_heading(3, "开始提取")
    blockers = _run_blockers(
        eff_src is not None,
        cfg,
        st.session_state.task,
        needs_embedding=eff_gl is not None and not cfg.no_translate,
    )
    if st.session_state.get("_col_errors"):
        blockers.append("修正无法读取的输入文件")
    can_run = not blockers
    if blockers:
        st.info("开始前还需：" + "；".join(blockers) + "。")
    elif eff_gl is None:
        st.info("未上传参考术语表：本次仍可正常提取，英文译文将由 AI 生成。")

    col_btn1, col_btn2, _ = st.columns([1.4, 1.15, 4])
    with col_btn1:
        if st.button("开始提取术语", type="primary", disabled=not can_run, width="stretch",
                     help=None if can_run else "请先完成页面列出的必需项"):
            tmp_dir = Path(tempfile.mkdtemp())
            src_path = tmp_dir / eff_src_name
            src_path.write_bytes(eff_src)
            if eff_gl is not None:
                gl_path = tmp_dir / eff_gl_name
                gl_path.write_bytes(eff_gl)
            else:
                gl_path = tmp_dir / "_empty_reference_glossary.xlsx"
                gl_path.write_bytes(empty_glossary_bytes())

            run_gl_cn_col = gl_cn_col if eff_gl is not None else 0
            run_gl_en_col = gl_en_col if eff_gl is not None else 1
            task = ProcessingTask()
            task.start(str(src_path), str(gl_path), cfg,
                       src_col=src_col, gl_cn_col=run_gl_cn_col, gl_en_col=run_gl_en_col,
                       src_en_col=src_en_col, src_bytes=eff_src,
                       key_col=None if key_col < 0 else key_col)
            try:
                from core.header_detect import record_source_correction, record_glossary_correction
                _ci = st.session_state.get("_col_info", {}) or {}
                detect_service = resolve_service_settings(cfg, "detect")
                detected_src = _ci.get("src", {})
                selected_key_col = None if key_col < 0 else key_col
                selected_en_col = src_en_col if cfg.bilingual else None
                source_changed = bool(detected_src) and (
                    detected_src.get("manual_preserved")
                    or src_col != detected_src.get("text_col")
                    or selected_key_col != detected_src.get("key_col")
                    or selected_en_col != detected_src.get("en_col")
                )
                if source_changed and _ci.get("src_headers"):
                    record_source_correction(
                        _ci["src_headers"],
                        src_col,
                        key_col=selected_key_col,
                        en_col=selected_en_col,
                        df=pd.read_excel(BytesIO(eff_src), nrows=3),
                        base_url=detect_service.api_base,
                        model=detect_service.model,
                    )
                detected_gl = _ci.get("gl", {})
                glossary_changed = bool(detected_gl) and (
                    detected_gl.get("manual_preserved")
                    or gl_cn_col != detected_gl.get("cn_col")
                    or gl_en_col != detected_gl.get("en_col")
                )
                if eff_gl is not None and glossary_changed and _ci.get("gl_headers"):
                    record_glossary_correction(
                        _ci["gl_headers"],
                        gl_cn_col,
                        gl_en_col,
                        df=pd.read_excel(BytesIO(eff_gl), nrows=3),
                        base_url=detect_service.api_base,
                        model=detect_service.model,
                    )
            except Exception:
                st.session_state._mapping_cache_warning = "列映射记忆失败，本次提取不受影响。"
            st.session_state.task = task
            st.session_state.task_results = None
            st.session_state._results_saved = False
            st.rerun()

    with col_btn2:
        task: ProcessingTask = st.session_state.task
        running = task is not None and task.is_running
        if st.button("取消本次任务", disabled=not running, width="stretch"):
            if task:
                task.cancel()
            st.rerun()

    st.divider()

    task: ProcessingTask = st.session_state.task
    if task is not None:

        @st.fragment(run_every=0.5)
        def _progress_fragment():
            if task._cancelling and not task.done:
                st.warning("正在取消任务；当前 API 请求结束后会安全停止，请勿关闭页面或启动新任务。")
                return

            if task.done:
                if task.error:
                    if "Cancelled" in str(task.error):
                        st.warning("任务已取消，可稍后从可恢复任务继续。")
                    else:
                        st.error(f"提取失败：{task.error}。请检查输入文件和 API 设置后重试。")
                else:
                    st.success(f"提取完成：共获得 {len(task.results)} 条术语，可在“结果与导出”中查看和下载。")
                    st.session_state.task_results = task.results
                    st.session_state._results_saved = True
                st.session_state.task = None
                return

            stage_labels = {
                "starting": "初始化中…",
                "loading": "加载文件…",
                "extracting": "AI 正在提取术语（3 轮一致性判断）…",
                "translating": "正在沿用参考译文并生成新译文…",
                "done": "完成",
            }

            label = stage_labels.get(task.stage, task.stage)
            total = task.stage_total
            done = task.stage_done
            if total > 1:
                pct = min(done / total, 1.0)
                st.progress(pct, text=f"{label} — {done}/{total}")
            elif done >= total and total > 0:
                st.progress(1.0, text=label)
            else:
                st.progress(0.0, text=label)

            if task.info:
                st.caption(task.info)

        _progress_fragment()

    if st.session_state.task_results is not None and st.session_state.task_results:
        st.divider()
        st.subheader("本次提取预览")
        df = pd.DataFrame(st.session_state.task_results)
        cols = ["term", "category", "translation", "match_type"]
        display_cols = [c for c in cols if c in df.columns]
        if display_cols:
            preview_df = df[display_cols].rename(columns={
                "term": "中文术语",
                "category": "术语分类",
                "translation": "英文译文",
                "match_type": "译文来源",
            })
            if "译文来源" in preview_df.columns:
                preview_df["译文来源"] = preview_df["译文来源"].map(MATCH_TYPE_SOURCE_LABELS).fillna("其他")
            st.dataframe(preview_df, width="stretch", height=400, hide_index=True)

# ═══════════════════════════════════════════════════════════
# Page: Results
# ═══════════════════════════════════════════════════════════

elif page_id == "results":
    _page_intro(
        "RESULT REVIEW",
        "结果与导出",
        "查看术语分类与译文来源，下载标注模板，或检查包含处理字段的完整结果。",
    )

    results = st.session_state.task_results

    if results is None or len(results) == 0:
        st.info("本次会话暂无结果。请先在“提取术语”页面运行任务，或查看下方历史结果。")
        out_root = ROOT / "output"
        saved = sorted(
            out_root.glob("run_*/results.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True
        ) if out_root.exists() else []
        if saved:
            st.header("历史结果")
            history_page_size = 10
            history_pages = (len(saved) + history_page_size - 1) // history_page_size
            history_page = 1
            if history_pages > 1:
                history_page_options = _ensure_widget_choice(
                    "_history_page", range(1, history_pages + 1), 1
                )
                history_page = st.selectbox(
                    "历史结果页",
                    history_page_options,
                    key="_history_page",
                    format_func=lambda value: f"第 {value}/{history_pages} 页",
                )
            history_start = (history_page - 1) * history_page_size
            visible_results = saved[history_start:history_start + history_page_size]
            for f in visible_results:
                run_name = f.parent.name
                ts = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                c1, c2, c3 = st.columns([4, 1, 1])
                c1.caption(f"`{run_name}` — {ts}")
                with c2:
                    st.download_button("下载结果", data=f.read_bytes(),
                                       file_name=f"{run_name}_results.xlsx",
                                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       key=f"dl_{f.name}", width="stretch")
                with c3:
                    if st.button("查看结果", key=f"load_{f.name}", width="stretch"):
                        st.session_state.task_results = pd.read_excel(f).to_dict("records")
                        st.rerun()
    else:
        df = pd.DataFrame(results)
        match_labels = MATCH_TYPE_SOURCE_LABELS
        match_counts = df["match_type"].value_counts() if "match_type" in df.columns else pd.Series(dtype="int64")
        categories = (
            df["category"].fillna("未分类").replace("", "未分类")
            if "category" in df.columns else pd.Series(dtype="object")
        )

        metric_cols = st.columns(4)
        metric_cols[0].metric("术语总数", len(results))
        metric_cols[1].metric("术语分类", int(categories.nunique()))
        metric_cols[2].metric("沿用术语表", int(match_counts.get("exact", 0)))
        metric_cols[3].metric("AI 生成译文", int(match_counts.get("llm_translated", 0)))

        cache = st.session_state.get("_dl_cache")
        template_filename = descriptive_template_filename(
            profile_name=st.session_state.cfg.profile,
            source_path=getattr(st.session_state.get("task"), "source_path", "")
                        or st.session_state.get("_src_cache", ("", b""))[0],
        )

        if not cache or cache[0] is not results:
            cache = (results, template_workbook_bytes(results), results_workbook_bytes(results))
            st.session_state._dl_cache = cache

        st.header("导出文件")
        c_dl1, c_dl2 = st.columns(2)
        with c_dl1:
            st.download_button(
                "下载标注模板（.xlsx）",
                data=cache[1],
                file_name=template_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                width="stretch",
                help="8 列审核模板，可直接导入配套的术语标注工具。",
            )
        with c_dl2:
            st.download_button(
                "下载完整结果（.xlsx）",
                data=cache[2],
                file_name="results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
                help="包含译文来源、原文定位等处理字段，便于复核和分析。",
            )

        st.header("译文来源")
        if not match_counts.empty:
            source_order = ["沿用术语表", "AI 翻译", "沿用原文英文", "未生成译文", "其他"]
            source_counts = {label: 0 for label in source_order}
            for match_type, count in match_counts.items():
                source_label = match_labels.get(match_type, "其他")
                source_counts[source_label] += int(count)
            source_rows = []
            for source_label in source_order:
                count = source_counts[source_label]
                if not count:
                    continue
                source_rows.append({
                    "译文来源": source_label,
                    "数量": int(count),
                    "占比": float(count) / len(results),
                    "顺序": source_order.index(source_label),
                })
            source_df = pd.DataFrame(source_rows)
            source_palette = ["#2f6f4e", "#3f6f8c", "#6d5687", "#64727e", "#a25757"]
            source_chart = (
                alt.Chart(source_df)
                .mark_bar(size=30, cornerRadius=7)
                .encode(
                    x=alt.X(
                        "占比:Q",
                        stack="zero",
                        scale=alt.Scale(domain=[0, 1]),
                        axis=None,
                    ),
                    color=alt.Color(
                        "译文来源:N",
                        scale=alt.Scale(domain=source_order, range=source_palette),
                        legend=alt.Legend(
                            title=None,
                            orient="bottom",
                            direction="horizontal",
                            symbolType="square",
                            labelFont="PingFang SC",
                            labelColor="#526172",
                        ),
                    ),
                    order=alt.Order("顺序:Q", sort="ascending"),
                    tooltip=[
                        alt.Tooltip("译文来源:N"),
                        alt.Tooltip("数量:Q", format=",d"),
                        alt.Tooltip("占比:Q", format=".1%"),
                    ],
                )
                .properties(height=58)
                .configure_view(strokeOpacity=0)
            )
            st.altair_chart(source_chart, width="stretch", theme=None)
            st.caption("　·　".join(
                f"{row['译文来源']} {row['数量']} 条（{row['占比']:.1%}）"
                for row in source_rows
            ))
        else:
            st.caption("当前结果没有译文来源信息。")

        st.header("术语分类分布")
        if not categories.empty:
            category_counts = categories.value_counts()
            full_cat_df = category_counts.rename_axis("术语分类").reset_index(name="数量")
            full_cat_df["占比"] = (full_cat_df["数量"] / len(results)).map(lambda value: f"{value:.1%}")
            chart_counts = category_counts
            categories_grouped = len(category_counts) > 12
            grouped_category_label = ""
            if categories_grouped:
                grouped_category_label = f"其余 {len(category_counts) - 11} 类"
                chart_counts = pd.concat([
                    category_counts.head(11),
                    pd.Series({grouped_category_label: int(category_counts.iloc[11:].sum())}),
                ])
            cat_df = chart_counts.rename_axis("术语分类").reset_index(name="数量")
            cat_df["占比"] = cat_df["数量"] / len(results)
            cat_df["数值标签"] = cat_df.apply(
                lambda row: f"{int(row['数量'])} 条 · {row['占比']:.1%}", axis=1
            )
            max_count = int(cat_df["数量"].max())
            chart_height = max(220, len(cat_df) * 38)
            category_order = cat_df["术语分类"].tolist()
            base = alt.Chart(cat_df).encode(
                y=alt.Y(
                    "术语分类:N",
                    sort=category_order,
                    title=None,
                    axis=alt.Axis(labelLimit=240, labelPadding=10),
                ),
                x=alt.X(
                    "数量:Q",
                    title=None,
                    scale=alt.Scale(domain=[0, max(1, max_count * 1.34)]),
                    axis=alt.Axis(grid=True, tickMinStep=1, labelColor="#5d6976", gridColor="#e7ecef"),
                ),
                tooltip=[
                    alt.Tooltip("术语分类:N"),
                    alt.Tooltip("数量:Q", format=",d"),
                    alt.Tooltip("占比:Q", format=".1%"),
                ],
            )
            bars = base.mark_bar(size=22, cornerRadiusEnd=6).encode(
                color=alt.condition(
                    alt.datum["数量"] == max_count,
                    alt.value("#2f6f4e"),
                    alt.value("#527f98"),
                )
            )
            labels = base.mark_text(align="left", baseline="middle", dx=8, color="#445363", fontSize=12).encode(
                text="数值标签:N"
            )
            category_chart = (
                (bars + labels)
                .properties(height=chart_height)
                .configure_view(strokeOpacity=0)
                .configure_axis(domain=False, tickColor="#d9e2e8", labelFont="PingFang SC")
            )
            st.altair_chart(category_chart, width="stretch", theme=None)
            if categories_grouped:
                st.caption(
                    f"图表展示数量最多的 11 类，其余合并为“{grouped_category_label}”；"
                    "深绿色表示当前数量最多的分类。"
                )
                with st.expander("查看完整分类统计", expanded=False):
                    st.dataframe(
                        full_cat_df,
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "术语分类": st.column_config.TextColumn("术语分类", width="large"),
                            "数量": st.column_config.NumberColumn("数量", format="%d"),
                            "占比": st.column_config.TextColumn("占比", width="small"),
                        },
                    )
            else:
                st.caption("深绿色表示当前数量最多的分类；悬停可查看数量与占比。")
        else:
            st.caption("当前结果没有术语分类信息。")

        st.header("全部术语")
        display_df = df.rename(columns={
            "term": "中文术语",
            "category": "术语分类",
            "translation": "英文译文",
            "match_type": "译文来源",
            "source_key": "文本 ID",
            "source_text": "来源原文",
            "note": "备注",
            "vote_count": "一致票数",
            "ref_term": "参考术语",
            "ref_trans": "参考译文",
            "ref_sim": "参考相似度",
        })
        if "译文来源" in display_df.columns:
            display_df["译文来源"] = display_df["译文来源"].map(match_labels).fillna("其他")
        preferred_columns = [
            "中文术语", "术语分类", "英文译文", "译文来源", "文本 ID", "来源原文",
            "备注", "一致票数", "参考术语", "参考译文", "参考相似度",
        ]
        ordered_columns = [name for name in preferred_columns if name in display_df.columns]
        ordered_columns += [name for name in display_df.columns if name not in ordered_columns]
        display_df = display_df[ordered_columns]
        display_column_config = {
            "中文术语": st.column_config.TextColumn("中文术语", width="medium"),
            "术语分类": st.column_config.TextColumn("术语分类", width="medium"),
            "英文译文": st.column_config.TextColumn("英文译文", width="medium"),
            "译文来源": st.column_config.TextColumn("译文来源", width="small"),
            "文本 ID": st.column_config.TextColumn("文本 ID", width="small"),
            "来源原文": st.column_config.TextColumn("来源原文", width="large"),
            "备注": st.column_config.TextColumn("备注", width="large"),
            "一致票数": st.column_config.NumberColumn("一致票数", format="%d", width="small"),
            "参考相似度": st.column_config.NumberColumn("参考相似度", format="%.3f", width="small"),
        }
        st.dataframe(
            display_df,
            width="stretch",
            height=430,
            hide_index=True,
            column_config={
                name: config for name, config in display_column_config.items()
                if name in display_df.columns
            },
        )

# ═══════════════════════════════════════════════════════════
# Sidebar footer
# ═══════════════════════════════════════════════════════════

with st.sidebar:
    st.divider()
    cfg = st.session_state.cfg
    _sync_cfg()

    sidebar_values = {
        "model": escape(cfg.model or "未设置"),
        "profile": escape(cfg.profile or "未设置"),
        "ner": escape(resolve_service_settings(cfg, "ner").model or "未设置"),
        "embedding": escape(resolve_service_settings(cfg, "embedding").model or "未设置"),
        "detect": escape(resolve_service_settings(cfg, "detect").model or "未设置"),
    }
    st.markdown(
        f"""
        <div class="lg-sidebar-status" role="group" aria-label="当前运行配置">
            <div class="lg-sidebar-status__title">当前配置</div>
            <div class="lg-sidebar-status__row">
                <span class="lg-sidebar-status__label">主模型</span>
                <span class="lg-sidebar-status__value" title="{sidebar_values['model']}">{sidebar_values['model']}</span>
            </div>
            <div class="lg-sidebar-status__row">
                <span class="lg-sidebar-status__label">项目规则</span>
                <span class="lg-sidebar-status__value" title="{sidebar_values['profile']}">{sidebar_values['profile']}</span>
            </div>
            <div class="lg-sidebar-status__section">处理并发</div>
            <div class="lg-sidebar-status__row">
                <span class="lg-sidebar-status__label">AI 提取</span>
                <span class="lg-sidebar-status__value">{cfg.max_concurrent}</span>
            </div>
            <div class="lg-sidebar-status__row">
                <span class="lg-sidebar-status__label">向量编码</span>
                <span class="lg-sidebar-status__value">{cfg.embed_workers}</span>
            </div>
            <div class="lg-sidebar-status__section">辅助模型</div>
            <div class="lg-sidebar-status__row">
                <span class="lg-sidebar-status__label">NER</span>
                <span class="lg-sidebar-status__value" title="{sidebar_values['ner']}">{sidebar_values['ner']}</span>
            </div>
            <div class="lg-sidebar-status__row">
                <span class="lg-sidebar-status__label">Embedding</span>
                <span class="lg-sidebar-status__value" title="{sidebar_values['embedding']}">{sidebar_values['embedding']}</span>
            </div>
            <div class="lg-sidebar-status__row">
                <span class="lg-sidebar-status__label">表头识别</span>
                <span class="lg-sidebar-status__value" title="{sidebar_values['detect']}">{sidebar_values['detect']}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    main_signature = _service_signature(cfg, "main")
    if st.button("测试主生成服务连接", width="stretch"):
        with st.spinner("正在连接主生成服务…"):
            ok, msg = test_api_connection(cfg, "main")
            st.session_state.api_test_result = {
                "signature": main_signature,
                "ok": ok,
                "message": msg,
            }

    _show_api_test_result("api_test_result", main_signature)
