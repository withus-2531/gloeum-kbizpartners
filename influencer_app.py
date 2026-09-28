"""Streamlit UI for the separate Instagram influencer discovery mode."""

from datetime import datetime
import os

import streamlit as st

from buyer_core import make_search_provider
from country_catalog import COUNTRIES, country_name_ko
from apify_instagram_client import check_apify_connection
from influencer_core import CATEGORY_KEYWORDS, InfluencerConfig, collect_influencers, default_keywords
from influencer_exporter import build_influencer_excel


def _country_label(country: str) -> str:
    return f"{country_name_ko(country)} · {country}"


def render_influencer_app() -> None:
    st.info("Instagram 내부 사용자검색과 해시태그 게시물에서 후보를 먼저 찾고, Apify 원본 프로필로 국가·계정유형·분야·팔로워를 검증합니다. 웹 검색은 선택 보강으로만 사용합니다.")

    with st.sidebar:
        st.header("Instagram 데이터 연결")
        apify_token=os.getenv("APIFY_TOKEN","").strip()
        if apify_token:
            st.info("APIFY_TOKEN 등록됨 · 실제 연결은 아래 버튼으로 확인하세요.")
            if st.button("Apify 실제 연결 테스트",use_container_width=True,key="inf_apify_test"):
                with st.spinner("공개 프로필 1개로 Apify Actor 실행을 확인하고 있습니다."):
                    check=check_apify_connection(token=apify_token)
                if check.get("ok"):
                    st.success(f"Apify 연결 정상 · Actor 결과 {check.get('result_count',0)}개")
                else:
                    st.error(check.get("message") or "Apify 연결 실패")
                st.caption(f"단계 {check.get('stage','-')} · {check.get('elapsed_seconds',0)}초")
            st.caption("연결 테스트는 공개 프로필 1개를 실제 실행하므로 Apify에 Run이 생성되고 소액 사용량이 발생할 수 있습니다.")
        else:
            st.warning("Render 환경변수 APIFY_TOKEN이 필요합니다.")
        with st.expander("웹 검색 보강 · 선택",expanded=False):
            use_web_fallback=st.toggle("Brave/Tavily로 부족한 후보 보강",value=False,key="inf_web_fallback")
            provider_name=st.selectbox(
                "보조 검색엔진",
                ["Brave (권장)","Brave → Tavily 자동전환","Tavily"],
                key="inf_provider",
                disabled=not use_web_fallback,
            )
            brave_key=st.text_input("Brave Search API Key",value=os.getenv("BRAVE_SEARCH_API_KEY",""),type="password",key="inf_brave_key",disabled=not use_web_fallback)
            tavily_key=st.text_input("Tavily API Key",value=os.getenv("TAVILY_API_KEY",""),type="password",key="inf_tavily_key",disabled=not use_web_fallback)
            st.caption("기본 수집은 Apify만으로 실행됩니다. Instagram 후보가 부족할 때만 켜세요.")
        st.caption("API 키는 환경변수 또는 현재 브라우저 세션에서만 사용하고 결과 파일에는 저장하지 않습니다.")

    left,right=st.columns([1,1])
    with left:
        country=st.selectbox("1단계 · 대상 국가",list(COUNTRIES),index=list(COUNTRIES).index("Vietnam"),format_func=_country_label,key="inf_country")
        category=st.selectbox("2단계 · 인플루언서 분야",list(CATEGORY_KEYWORDS),key="inf_category")
        keyword_key="inf_keywords"
        if st.session_state.get("inf_keyword_category")!=category or keyword_key not in st.session_state:
            st.session_state[keyword_key]="\n".join(default_keywords(category))
            st.session_state["inf_keyword_category"]=category
        keyword_text=st.text_area("3단계 · 검색 키워드 · 한 줄에 하나",key=keyword_key,height=150)
        keywords=list(dict.fromkeys(line.strip() for line in keyword_text.splitlines() if line.strip()))
        target_count=st.select_slider("4단계 · 후보 수",options=[10,20,50,100],value=10,help="처음에는 10개 시험수집 후 검수하고 20개, 100개 순서로 확대합니다.")
    with right:
        st.markdown("**5단계 · 후보 조건**")
        f1,f2=st.columns(2)
        with f1:
            min_followers=st.number_input("최소 팔로워 · 0=제한 없음",min_value=0,max_value=100_000_000,value=0,step=1000)
        with f2:
            max_followers=st.number_input("최대 팔로워 · 0=제한 없음",min_value=0,max_value=1_000_000_000,value=0,step=1000)
        include_business=st.toggle("SNS 셀러·사업계정도 포함",value=False,help="기본값은 인플루언서·크리에이터 후보만 수집합니다.")
        use_apify=st.toggle("Apify 원본 프로필 자동검증",value=True,help="발견된 계정명을 Profile Scraper에 자동 전달해 공개 프로필·팔로워·소개·최근 게시물을 확인합니다.")
        verify_external=st.toggle("외부 공개출처 추가 교차검증",value=False,help="Apify 근거가 부족한 후보를 공개 웹페이지로 한 번 더 확인합니다. 검색 호출이 추가됩니다.")
        if use_web_fallback:
            max_queries=st.select_slider("웹 보강 최대 호출",options=[2,4,6,8],value=4,help="Instagram 내부 발견 결과가 부족할 때만 실행합니다.")
        else:
            max_queries=1
        st.caption("팔로워 수가 검색결과에 표시되지 않은 계정은 제외하지 않고 ‘추가확인’으로 남깁니다.")
        st.caption(f"Instagram 내부 검색 · {country_name_ko(country)} · {category} · 키워드 {len(keywords)}개 · 후보 {target_count}개")

    with st.expander("DM·이메일 발신정보",expanded=True):
        s1,s2=st.columns(2)
        with s1:
            company_name=st.text_input("영문 회사명",key="inf_company")
            brand_name=st.text_input("영문 브랜드명",key="inf_brand")
            contact_name=st.text_input("영문 담당자명",key="inf_contact")
            website=st.text_input("회사·브랜드 홈페이지",key="inf_website")
        with s2:
            product_name=st.text_input("영문 제품명",key="inf_product")
            product_category=st.text_input("영문 제품 카테고리",value="K-beauty product" if category=="뷰티·화장품" else "Korean product",key="inf_product_category")
            selling_points=st.text_area("핵심 특징 · 사실만 입력",key="inf_selling_points",height=72)
            collaboration_offer=st.text_area("협업 제안 문장 · 선택",placeholder="Would you be open to receiving samples and discussing a possible review collaboration?",key="inf_offer",height=72)
        st.caption("브랜드명과 제품명을 입력하면 후보별 영문 DM·이메일 초안이 자동 생성됩니다.")

    button_col,reset_col=st.columns([3,1])
    with button_col:
        start=st.button("Instagram 인플루언서 검색 시작",type="primary",use_container_width=True)
    with reset_col:
        reset=st.button("결과 초기화",use_container_width=True)
    if reset:
        st.session_state.pop("influencer_payload",None)
        st.rerun()

    if start:
        provider_key_ok=(bool(brave_key) if provider_name=="Brave (권장)" else bool(tavily_key) if provider_name=="Tavily" else bool(brave_key or tavily_key))
        if use_web_fallback and not provider_key_ok:
            st.error("웹 검색 보강을 켰다면 선택한 검색엔진의 API Key가 필요합니다.")
            st.stop()
        if not apify_token:
            st.error("Instagram 후보 발견과 검증을 위해 Render Environment에 APIFY_TOKEN을 등록하세요.")
            st.stop()
        if not keywords:
            st.error("검색 키워드를 1개 이상 입력하세요.")
            st.stop()
        if max_followers and min_followers and max_followers<min_followers:
            st.error("최대 팔로워는 최소 팔로워보다 작을 수 없습니다.")
            st.stop()
        if not brand_name or not product_name:
            st.warning("브랜드명 또는 제품명이 비어 있어 검색은 진행하지만 DM·이메일 초안은 입력대기로 표시됩니다.")
        config=InfluencerConfig(
            country=country,
            category=category,
            keywords=keywords,
            target_count=int(target_count),
            min_followers=int(min_followers),
            max_followers=int(max_followers),
            max_queries=int(max_queries),
            include_business_accounts=bool(include_business),
            verify_original_profiles=False,
            verify_external_evidence=bool(verify_external),
            max_external_pages=2,
            search_provider=provider_name.replace(" (권장)",""),
            use_apify_verification=bool(use_apify),
            apify_batch_size=40,
            use_apify_discovery=True,
            use_web_fallback=bool(use_web_fallback),
            apify_search_limit=12,
            apify_hashtag_limit=10,
            max_runtime_seconds=330,
        )
        sender={"company_name":company_name,"brand_name":brand_name,"contact_name":contact_name,"website":website}
        product={"product_name":product_name,"category":product_category,"selling_points":selling_points,"collaboration_offer":collaboration_offer}
        provider=make_search_provider(config.search_provider,brave_key=brave_key,tavily_key=tavily_key) if use_web_fallback else None
        progress_bar=st.progress(0,text="Instagram 내부 후보 발견을 준비하고 있습니다.")
        def on_progress(done,total,message):
            progress_bar.progress(min(done/max(total,1),1.0),text=message)
        with st.status("Instagram 후보 발견·검증 중",expanded=True) as status:
            try:
                payload=collect_influencers(config,provider,sender,product,on_progress,apify_token=apify_token)
            except Exception as exc:
                status.update(label="외부 API 오류로 검색 종료",state="error",expanded=True)
                st.error(f"검색을 완료하지 못했습니다: {type(exc).__name__}: {exc}")
                if "TIMED-OUT" in str(exc).upper() or "TIMEOUT" in str(exc).upper():
                    st.info("Instagram 검색 Actor가 제한시간을 넘겼습니다. 앱은 핵심 검색어 1개로 자동 재시도합니다. 재시도도 실패하면 잠시 후 다시 실행하세요.")
                else:
                    st.info("인증·잔액·권한 오류는 자동 재시도로 해결되지 않습니다. 위 오류 문구를 보관하고 Apify Runs와 Render 로그를 확인하세요.")
                st.stop()
            status.update(label="Instagram 후보 검색 완료",state="complete",expanded=False)
        st.session_state.pop("inf_selected_record",None)
        st.session_state["influencer_payload"]=payload

    payload=st.session_state.get("influencer_payload")
    if not payload:
        st.caption("검색 전입니다. 베트남 뷰티 분야 20개 시험수집부터 시작하는 것을 권장합니다.")
        return
    if st.session_state.pop("inf_status_saved",False):
        st.success("연락 상태를 현재 세션과 다운로드 Excel에 반영했습니다.")

    records=payload.get("records",[]); review_records=payload.get("review_records",[]); summary=payload.get("summary",{})
    st.subheader("Instagram 인플루언서 후보")
    m1,m2,m3,m4,m5,m6=st.columns(6)
    m1.metric("최종 후보",summary.get("records",0)); m2.metric("A 우선",summary.get("tier_a",0))
    m3.metric("B 검토",summary.get("tier_b",0)); m4.metric("검토대기",summary.get("review_records",0))
    m5.metric("공개 이메일",summary.get("public_emails",0)); m6.metric("DM 초안",summary.get("dm_drafts",0))
    c1,c2,c3=st.columns(3)
    c1.metric("국가 확인",summary.get("country_verified",0))
    c2.metric("위치 미확인",summary.get("country_unverified",0))
    c3.metric("100개 확대", "가능" if summary.get("quality_gate_passed") else "금지")
    st.caption(f"1차 품질게이트 · A/B 최소 {summary.get('quality_gate_required',10)}개 필요")
    st.caption(f"Instagram 발견 {summary.get('instagram_discovered_handles',0)}계정 · 사용자검색 결과 {summary.get('instagram_search_results',0)}개 · 해시태그 게시물 {summary.get('instagram_hashtag_posts',0)}개 · 웹 보강 {summary.get('web_fallback_calls',0)}회")
    st.caption(f"원본 프로필 확인 {summary.get('profile_checks',0)}개 · Apify 단계 {summary.get('apify_stages',0)}회 · 신규요청 {summary.get('apify_requested',0)}개 · 캐시 재사용 {summary.get('apify_cache_hits',0)}개 · 검증완료 {summary.get('apify_verified',0)}개 · 확인불가 {summary.get('apify_unavailable',0)}개 · 지역명이 계정명에만 있으면 국가 확인으로 인정하지 않음")
    st.caption(f"외부 공개출처 확인 {summary.get('external_pages_checked',0)}개 · 근거 보강 {summary.get('external_pages_verified',0)}개 · 확인 실패 {summary.get('external_pages_failed',0)}개 · 정확한 계정명이 없는 페이지는 근거로 사용하지 않음")
    if not summary.get("quality_gate_passed"):
        st.error("품질 검수 미통과 · 국가 확인률 또는 후보 수가 기준에 미달합니다. 100개로 확대하지 마세요.")
    if len(records)<int(payload.get("config",{}).get("target_count",0)):
        st.warning(f"목표 {payload.get('config',{}).get('target_count',0)}개 중 {len(records)}개 확보 · 키워드 또는 검색 호출 수를 늘려 보충할 수 있습니다.")
    table=[{
        "순위":r.get("rank"),"등급":r.get("grade"),"점수":r.get("score"),"계정":f"@{r.get('handle','')}",
        "표시명":r.get("display_name"),"유형":r.get("profile_type"),"대상국가":r.get("target_country"),
        "확인국가":r.get("verified_country") or "미확인","국가판정":r.get("country_status"),"팔로워":r.get("followers_display"),
        "원본확인":r.get("profile_verification_status") or "미실행",
        "공개 이메일":r.get("public_email") or "미확보","Instagram":r.get("instagram_url"),
        "일치 키워드":r.get("matched_keywords"),"DM 상태":r.get("dm_status"),"이메일 상태":r.get("email_contact_status"),
    } for r in records]
    st.dataframe(
        table,use_container_width=True,hide_index=True,
        column_config={"Instagram":st.column_config.LinkColumn("Instagram",display_text="프로필 열기")},
    )
    if review_records:
        with st.expander(f"검토대기 후보 {len(review_records)}개 · 최종 후보와 연락 목록에는 포함되지 않음"):
            st.dataframe([{
                "계정":f"@{r.get('handle','')}","표시명":r.get("display_name"),
                "국가판정":r.get("country_status"),"국가근거":r.get("country_evidence"),
                "유형":r.get("profile_type"),"원본확인":r.get("profile_verification_status") or "미실행","검토사유":r.get("profile_review_reason") or "", "Instagram":r.get("instagram_url"),
            } for r in review_records],use_container_width=True,hide_index=True,
            column_config={"Instagram":st.column_config.LinkColumn("Instagram",display_text="프로필 열기")})

    excel=build_influencer_excel(payload)
    filename=f"GLOEUM_Instagram_{payload.get('config',{}).get('country','')}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    st.download_button("Instagram 후보 Excel 다운로드",excel,file_name=filename,mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",use_container_width=True,on_click="ignore")

    if not records:
        st.info("조건에 맞는 공개 프로필 후보가 없습니다. 검색 키워드나 팔로워 범위를 조정하세요.")
        return
    labels={f"{r.get('rank')}. @{r.get('handle')} · {r.get('display_name')} · {r.get('grade')}등급":index for index,r in enumerate(records)}
    selected_label=st.selectbox("연락할 계정 선택",list(labels),key="inf_selected_record")
    index=labels[selected_label]; record=records[index]
    st.markdown(f"### @{record.get('handle')} 연락 준비")
    a1,a2=st.columns(2)
    with a1:
        st.link_button("Instagram 프로필 열기",record.get("instagram_url"),use_container_width=True)
    with a2:
        if record.get("email_compose_url"):
            st.link_button("이메일 작성창 열기",record.get("email_compose_url"),use_container_width=True)
        else:
            st.button("공개 이메일 미확보",disabled=True,use_container_width=True)
    st.caption("DM 초안 오른쪽 상단의 복사 아이콘을 눌러 복사한 뒤, 열린 Instagram 프로필에서 내용을 확인하고 직접 발송하세요.")
    st.code(record.get("dm_draft",""),language=None,wrap_lines=True)
    if record.get("public_email"):
        with st.expander("이메일 제목·본문 확인",expanded=False):
            st.text_input("제목",value=record.get("email_subject",""),disabled=True,key=f"inf_subject_{record.get('handle')}")
            st.text_area("본문",value=record.get("email_body",""),height=260,disabled=True,key=f"inf_body_{record.get('handle')}")

    status1,status2,save_col=st.columns([2,2,1])
    dm_options=["초안","검수완료","발송완료","회신","제외"]
    email_options=["미발송","검수완료","발송완료","회신","이메일 없음","제외"]
    with status1:
        dm_status=st.selectbox("DM 상태",dm_options,index=dm_options.index(record.get("dm_status","초안")) if record.get("dm_status") in dm_options else 0,key=f"dm_status_{record.get('handle')}")
    with status2:
        current_email_status=record.get("email_contact_status","미발송")
        email_status=st.selectbox("이메일 연락 상태",email_options,index=email_options.index(current_email_status) if current_email_status in email_options else 0,key=f"email_status_{record.get('handle')}")
    with save_col:
        st.write("")
        st.write("")
        if st.button("상태 저장",use_container_width=True):
            payload["records"][index]["dm_status"]=dm_status
            payload["records"][index]["email_contact_status"]=email_status
            st.session_state["influencer_payload"]=payload
            st.session_state["inf_status_saved"]=True
            st.rerun()

    if payload.get("errors"):
        with st.expander(f"검색 오류·참고 {len(payload['errors'])}건"):
            for error in payload["errors"]:
                st.write("-",error)
    st.caption("Instagram 내부에서 발견한 후보를 Apify 공개 프로필 데이터로 자동검증합니다. 연락 직전에는 계정 운영 여부와 공개 연락처만 최종 확인하세요.")
