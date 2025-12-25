import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
import os
import json
import io
import numpy as np
import time

# PDF 생성 라이브러리
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors

# ---------------------------------------------------------
# 1. 설정 및 초기화
# ---------------------------------------------------------
st.set_page_config(page_title="교수학습공동체 업무 자동화", layout="wide")

# 한글 폰트 설정
FONT_PATH = "NanumGothic.ttf"
if not os.path.exists(FONT_PATH):
    st.error(f"⚠️ '{FONT_PATH}' 폰트 파일이 없습니다. PDF 생성 시 한글이 깨질 수 있습니다.")

# OpenAI 설정
openai_client = None
if "openai" in st.secrets:
    try:
        openai_client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    except Exception as e:
        st.error(f"OpenAI 설정 오류: {e}")

# 구글 시트 연결 설정
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
SHEET_NAME = "교수학습공동체_DB" 

@st.cache_resource(ttl=3600)
def init_gsheet_connection():
    try:
        creds_dict = dict(st.secrets["connections"]["gsheets"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
        client = gspread.authorize(creds)
        sh = client.open(SHEET_NAME)
        return sh
    except Exception as e:
        st.error(f"❌ 구글 시트 연결 실패: {e}")
        return None

def get_worksheet(tab_name):
    sh = init_gsheet_connection()
    if sh is None: return None
    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=100, cols=10)
        if tab_name == "재직교수":
            ws.append_row(["연번", "학과", "직급", "이름"])
        elif tab_name == "회의록":
            ws.append_row(["ID", "연번", "날짜", "시간", "장소", "주제", "참석자_텍스트", "참석자_JSON", "내용", "키워드"])
    except gspread.exceptions.APIError:
        time.sleep(1)
        st.warning("⚠️ 구글 연결 불안정. 잠시 후 다시 시도하세요.")
        return None
    return ws

def get_sheet_url():
    sh = init_gsheet_connection()
    return sh.url if sh else None

def init_settings_sheet():
    ws = get_worksheet("설정")
    if ws:
        try:
            headers = ws.row_values(1)
            if not headers or headers != ["Key", "Value"]:
                ws.clear() 
                ws.append_row(["Key", "Value"])
                ws.append_row(["admin_pw", DEFAULT_PW["admin"]])
                ws.append_row(["user_pw", DEFAULT_PW["user"]])
        except: pass

# [수정] 데이터 로드 시 컬럼 누락 방지 강화
def load_data(tab_name):
    ws = get_worksheet(tab_name)
    
    # 기본 컬럼 정의
    cols = []
    if tab_name == "재직교수": 
        cols = ["연번", "학과", "직급", "이름"]
    elif tab_name == "회의록": 
        cols = ["ID", "연번", "날짜", "시간", "장소", "주제", "참석자_텍스트", "참석자_JSON", "내용", "키워드"]

    if not ws:
        return pd.DataFrame(columns=cols)

    try:
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        
        # 데이터가 비었거나 컬럼이 하나도 없으면 강제로 컬럼 설정
        if df.empty or len(df.columns) == 0:
            df = pd.DataFrame(columns=cols)
        
        # [중요] 특정 필수 컬럼이 없는 경우(헤더 손상 등) 대비
        if tab_name == "회의록" and "ID" not in df.columns:
            # 빈 데이터프레임으로 리셋하거나 경고 (여기선 빈 DF 리턴하여 에러 방지)
            return pd.DataFrame(columns=cols)
            
        return df
    except: 
        return pd.DataFrame(columns=cols)

def save_row(tab_name, row_data):
    ws = get_worksheet(tab_name)
    if ws:
        cleaned_data = [int(x) if isinstance(x, (np.integer, np.int64)) else x for x in row_data]
        ws.append_row(cleaned_data)

def delete_row(tab_name, id_col_name, target_id):
    ws = get_worksheet(tab_name)
    if not ws: return False
    try:
        cell = ws.find(str(target_id))
        if cell:
            ws.delete_rows(cell.row)
            return True
    except: return False
    return False

def update_row_by_id(tab_name, target_id, new_data_list):
    ws = get_worksheet(tab_name)
    if not ws: return False, "연결 실패"
    try:
        cell = ws.find(str(target_id), in_column=1) 
        if cell:
            cleaned_data = [int(x) if isinstance(x, (np.integer, np.int64)) else x for x in new_data_list]
            end_col_char = chr(64 + len(cleaned_data))
            cell_range = f"A{cell.row}:{end_col_char}{cell.row}"
            ws.update(range_name=cell_range, values=[cleaned_data])
            return True, "성공"
        return False, "ID 없음"
    except Exception as e: return False, str(e)

def update_faculty_row(target_no, new_dept, new_rank, new_name):
    ws = get_worksheet("재직교수")
    if not ws: return False
    try:
        cell = ws.find(str(target_no), in_column=1)
        if cell:
            ws.update_cell(cell.row, 2, new_dept)
            ws.update_cell(cell.row, 3, new_rank)
            ws.update_cell(cell.row, 4, new_name)
            return True
        return False
    except: return False

def update_row_by_date(tab_name, target_date, new_data_list):
    ws = get_worksheet(tab_name)
    if not ws: return False
    try:
        cell = ws.find(target_date, in_column=3) 
        if cell:
            cleaned_data = [int(x) if isinstance(x, (np.integer, np.int64)) else x for x in new_data_list]
            end_col_char = chr(64 + len(cleaned_data))
            cell_range = f"A{cell.row}:{end_col_char}{cell.row}"
            ws.update(range_name=cell_range, values=[cleaned_data])
            return True
        return False
    except: return False

# ---------------------------------------------------------
# 2. 인증 및 비밀번호
# ---------------------------------------------------------
DEFAULT_PW = {"admin": "삼막로155", "user": "2601"}

def get_passwords():
    init_settings_sheet() 
    df = load_data("설정")
    pw_dict = DEFAULT_PW.copy()
    if not df.empty:
        for idx, row in df.iterrows():
            if row.get('Key') == 'admin_pw':
                pw_dict['admin'] = str(row.get('Value'))
            elif row.get('Key') == 'user_pw':
                pw_dict['user'] = str(row.get('Value'))
    return pw_dict

def update_password(role, new_pw):
    ws = get_worksheet("설정")
    if ws:
        try:
            cell = ws.find(f"{role}_pw")
            if cell: ws.update_cell(cell.row, 2, new_pw)
            else: ws.append_row([f"{role}_pw", new_pw])
            st.cache_data.clear()
        except: pass

# ---------------------------------------------------------
# 3. 로직 함수 (AI, PDF, CSV)
# ---------------------------------------------------------
def generate_ai_minutes(topic, keywords):
    if not openai_client: return "OpenAI API Key 설정 필요"
    prompt = f"""
    작성 요청: 아래 주제와 키워드를 바탕으로 핵심 회의 내용을 정리해줘.
    [입력 데이터] 주제: {topic}, 키워드: {keywords}
    [제약사항] 번호 없이 하이픈(-) 사용. '~함', '~음' 등 명사형 개조식. 일시/장소/참석자 제외. 10줄 내외.
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role": "system", "content": "너는 대학 행정 회의록 전문 서기야."}, {"role": "user", "content": prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content.replace("**", "").strip()
    except Exception as e: return f"AI 생성 오류: {e}"

def create_signature_pdf(meeting_rows):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    try:
        pdfmetrics.registerFont(TTFont('Nanum', FONT_PATH))
        font_name = 'Nanum'
    except: font_name = 'Helvetica'

    for i, meeting in enumerate(meeting_rows):
        if i > 0: c.showPage()
        try:
            dt = datetime.strptime(meeting['날짜'], "%Y-%m-%d")
            days = ["월", "화", "수", "목", "금", "토", "일"]
            day_str = days[dt.weekday()]
            time_parts = meeting['시간'].split('~')
            start_t = time_parts[0].strip().replace(":", "시 ") + "분"
            end_t = time_parts[1].strip().replace(":", "시 ") + "분"
            full_date_str = f"{dt.year}년 {dt.month}월 {dt.day}일({day_str}요일) {start_t} - {end_t}"
        except: full_date_str = f"{meeting['날짜']} {meeting['시간']}"

        c.setFont(font_name, 14)
        c.drawString(20 * mm, height - 25 * mm, "<교수학습방법개선 공동체 운영>") 
        c.setFont(font_name, 20)
        c.drawCentredString(width / 2, height - 45 * mm, "회의참석자 서명부")
        c.setFont(font_name, 11)
        c.drawString(25 * mm, height - 65 * mm, f"■ 일시: {full_date_str}")
        c.drawString(25 * mm, height - 73 * mm, f"■ 장소: {meeting['장소']}")

        table_data = [["연번", "소속학과명", "직급", "성명", "자필서명\n(도장날인X)", "비고"]]
        attendees = []
        try: attendees = json.loads(meeting['참석자_JSON'])
        except: pass

        for idx, person in enumerate(attendees, 1):
            table_data.append([str(idx), person.get('학과', ''), person.get('직급', ''), person.get('이름', ''), "", ""])
        while len(table_data) < 11: table_data.append(["", "", "", "", "", ""])

        t = Table(table_data, colWidths=[15*mm, 40*mm, 30*mm, 30*mm, 45*mm, 20*mm], rowHeights=13*mm)
        t.setStyle(TableStyle([
            ('FONT', (0, 0), (-1, -1), font_name, 10),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ]))
        t.wrapOn(c, width, height)
        t.drawOn(c, 20 * mm, height - 90 * mm - (len(table_data) * 13 * mm))
    c.save()
    buffer.seek(0)
    return buffer

def create_csv_export(meeting_rows):
    export_list = []
    for meeting in meeting_rows:
        try:
            dt = datetime.strptime(meeting['날짜'], "%Y-%m-%d")
            days = ["월", "화", "수", "목", "금", "토", "일"]
            short_year = dt.year % 100
            time_str = meeting['시간'].replace(" ", "").replace("~", " ~ ")   
            formatted_date = f"{short_year}.{dt.month}.{dt.day}.({days[dt.weekday()]}), {time_str}"
        except: formatted_date = f"{meeting['날짜']}, {meeting['시간']}"
            
        attendees_str = meeting['참석자_텍스트'].replace(", ", "\n").replace(",", "\n")
        content_str = meeting['내용']
        if content_str and isinstance(content_str, str):
            if content_str.strip().startswith(("-", "=", "+")):
                content_str = "'" + content_str

        export_list.append({
            "일시": formatted_date, 
            "장소": meeting['장소'], 
            "주제": meeting['주제'],
            "참석자(3명 이상)": attendees_str, 
            "회의 내용(2줄 이상, 구체적으로 작성)": content_str,
            "증빙자료": "서명부\n첨부"
        })
    return pd.DataFrame(export_list)

# ---------------------------------------------------------
# 4. 공통: 회의록 수정 폼 렌더링
# ---------------------------------------------------------
def render_meeting_edit_form(df_m, faculty_options, key_suffix, current_id):
    st.markdown("---")
    if st.button("⬅️ 수정 취소 및 목록으로 돌아가기", key=f"btn_top_back_{key_suffix}"):
        if key_suffix == 'mng': st.session_state['mng_edit_id'] = None
        st.rerun()

    st.subheader(f"✏️ 회의록 수정 (ID: {current_id})")
    
    # [수정] KeyError 방지: ID 컬럼이 없거나 해당 ID가 없을 경우 안전하게 처리
    if 'ID' not in df_m.columns:
        st.error("데이터 오류: 'ID' 컬럼을 찾을 수 없습니다. 구글 시트 헤더를 확인해주세요.")
        return

    # ID 매칭
    filtered_df = df_m[df_m['ID'].astype(str) == str(current_id)]
    
    if filtered_df.empty:
        st.error(f"해당 ID({current_id})의 데이터를 찾을 수 없습니다. 이미 삭제되었을 수 있습니다.")
        if st.button("목록으로 복귀", key=f"btn_error_back_{key_suffix}"):
            if key_suffix == 'mng': st.session_state['mng_edit_id'] = None
            st.rerun()
        return

    target_row = filtered_df.iloc[0]
    
    try:
        date_obj = datetime.strptime(target_row['날짜'], "%Y-%m-%d")
        t_range = target_row['시간'].split('~')
        start_obj = datetime.strptime(t_range[0].strip(), "%H:%M")
        end_obj = datetime.strptime(t_range[1].strip(), "%H:%M")
    except:
        date_obj = datetime.today()
        start_obj = datetime.strptime("12:00", "%H:%M")
        end_obj = datetime.strptime("13:00", "%H:%M")

    ce1, ce2, ce3 = st.columns(3)
    e_date = ce1.date_input("날짜", date_obj, key=f"ed_d_{key_suffix}")
    e_start = ce2.time_input("시작", start_obj, key=f"ed_s_{key_suffix}")
    e_end = ce3.time_input("종료", end_obj, key=f"ed_e_{key_suffix}")
    
    e_place = st.text_input("장소", target_row['장소'], key=f"ed_p_{key_suffix}")
    e_topic = st.text_input("주제", target_row['주제'], key=f"ed_t_{key_suffix}")
    
    st.markdown(f"**현재 참석자:** {target_row['참석자_텍스트']}")
    default_sel = []
    try:
        saved_json = json.loads(target_row['참석자_JSON'])
        for person in saved_json:
            match = [opt for opt in faculty_options if opt.startswith(f"{person['이름']} ({person['학과']}")]
            if match: default_sel.append(match[0])
    except: pass

    e_sel_fac = st.multiselect("참석자 재선택 (수정 시 필수)", faculty_options, default=default_sel, key=f"ed_f_{key_suffix}")
    
    with st.expander("외부 인원 (수정 필요 시 입력)"):
        ce_m1, ce_m2, ce_m3 = st.columns(3)
        e_nm = ce_m1.text_input("이름", key=f"ed_mn_{key_suffix}")
        e_dp = ce_m2.text_input("학과", key=f"ed_md_{key_suffix}")
        e_rk = ce_m3.text_input("직급", key=f"ed_mr_{key_suffix}")
        e_add_man = st.checkbox("포함", key=f"ed_mc_{key_suffix}")

    e_content = st.text_area("내용", target_row['내용'], height=200, key=f"ed_c_{key_suffix}")
    
    col_save, col_cancel = st.columns(2)
    if col_save.button("수정 내용 저장", type="primary", key=f"btn_sv_{key_suffix}"):
        att_struct, att_txt = [], []
        if e_sel_fac:
            for it in e_sel_fac:
                try:
                    nm, info = it.split(' (')[0], it.split(' (')[1][:-1]
                    dp, rk = info.split('/')
                    att_struct.append({"이름": nm, "학과": dp, "직급": rk})
                    att_txt.append(f"{nm}({dp})")
                except: pass
        if e_add_man and e_nm:
            att_struct.append({"이름": e_nm, "학과": e_dp, "직급": e_rk})
            att_txt.append(f"{e_nm}({e_dp})")
        
        if not att_struct:
            final_json = target_row['참석자_JSON']
            final_txt = target_row['참석자_텍스트']
        else:
            final_json = json.dumps(att_struct, ensure_ascii=False)
            final_txt = ", ".join(att_txt)

        updated_row = [
            str(target_row['ID']), int(target_row['연번']), e_date.strftime("%Y-%m-%d"),
            f"{e_start.strftime('%H:%M')} ~ {e_end.strftime('%H:%M')}", e_place, e_topic,
            final_txt, final_json, e_content, target_row['키워드']
        ]
        
        success, msg = update_row_by_id("회의록", target_row['ID'], updated_row)
        if success:
            st.success("수정되었습니다.")
            if key_suffix == 'mng': st.session_state['mng_edit_id'] = None
            st.rerun()
        else: st.error(f"수정 실패: {msg}")

    if col_cancel.button("취소", key=f"btn_cc_{key_suffix}"):
        if key_suffix == 'mng': st.session_state['mng_edit_id'] = None
        st.rerun()

# ---------------------------------------------------------
# 5. 메인 UI
# ---------------------------------------------------------
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'user_role' not in st.session_state: st.session_state['user_role'] = None
if 'generated_content' not in st.session_state: st.session_state['generated_content'] = ""
if 'save_step' not in st.session_state: st.session_state['save_step'] = 'input'
if 'temp_data' not in st.session_state: st.session_state['temp_data'] = None
if 'mng_edit_id' not in st.session_state: st.session_state['mng_edit_id'] = None
if 'del_confirm_id' not in st.session_state: st.session_state['del_confirm_id'] = None
if 'fac_edit_mode' not in st.session_state: st.session_state['fac_edit_mode'] = False
if 'fac_edit_no' not in st.session_state: st.session_state['fac_edit_no'] = None

# [A] 로그인
if not st.session_state['logged_in']:
    st.title("🔒 교수학습공동체 시스템 로그인")
    c1, c2 = st.columns([1, 2])
    with c1: auth = st.radio("유형", ["관리자", "일반사용자"], key="rad_log")
    with c2: 
        pw = st.text_input("비밀번호", type="password", key="inp_pw")
        if st.button("로그인", type="primary", key="btn_log"):
            pws = get_passwords()
            if auth=="관리자" and pw==pws['admin']:
                st.session_state['logged_in']=True; st.session_state['user_role']="admin"; st.rerun()
            elif auth=="일반사용자" and pw==pws['user']:
                st.session_state['logged_in']=True; st.session_state['user_role']="user"; st.rerun()
            else: st.error("비밀번호 오류")
    st.stop()

# [B] 메인
ch1, ch2 = st.columns([3, 1.5])
with ch1: st.title("🏫 교수학습공동체 회의록 관리 시스템")
with ch2:
    st.markdown(f"<div style='text-align: right;'>👤 <b>{st.session_state['user_role']}</b> 모드</div>", unsafe_allow_html=True)
    if st.session_state['user_role'] == 'admin':
        hb1, hb2 = st.columns([1, 1])
        with hb1:
            url = get_sheet_url()
            if url: st.link_button("📂 구글 시트 보기", url, use_container_width=True)
            else: st.button("연결 실패", disabled=True, use_container_width=True)
        with hb2:
            if st.button("🚪 로그아웃", use_container_width=True): st.session_state.clear(); st.rerun()
    else:
        if st.button("🚪 로그아웃", use_container_width=True): st.session_state.clear(); st.rerun()
st.divider()

# [C] 탭 기능
faculty_df = load_data("재직교수")
faculty_options = [f"{row['이름']} ({row['학과']}/{row['직급']})" for idx, row in faculty_df.iterrows()] if not faculty_df.empty else []

# 일반 사용자 모드 (개요 + 검색)
if st.session_state['user_role'] == 'user':
    st.header("📅 회의록 일자별 개요")
    df = load_data("회의록")
    
    if not df.empty:
        # 날짜 내림차순
        df_overview = df.sort_values(by="날짜", ascending=False)
        # 표시 컬럼
        disp_cols = ['날짜', '시간', '주제', '참석자_텍스트']
        st.dataframe(
            df_overview[disp_cols],
            hide_index=True,
            use_container_width=True,
            column_config={
                "날짜": st.column_config.TextColumn("일자"),
                "시간": st.column_config.TextColumn("시간"),
                "주제": st.column_config.TextColumn("회의 주제"),
                "참석자_텍스트": st.column_config.TextColumn("참석자 명단", width="large")
            }
        )
    else: st.info("등록된 회의록이 없습니다.")

    st.divider()

    st.header("🔍 회의록 검색")
    c_s1, c_s2 = st.columns([1, 3])
    with c_s1: st_type = st.selectbox("검색 기준", ["전체", "이름", "학과", "주제", "내용"], key="search_type_usr")
    with c_s2: sk = st.text_input("검색어 입력", key="sk_usr")
    
    if sk and not df.empty:
        if st_type == "전체": mask = df['주제'].str.contains(sk) | df['참석자_텍스트'].str.contains(sk) | df['내용'].str.contains(sk)
        elif st_type == "이름": mask = df['참석자_텍스트'].str.contains(sk)
        elif st_type == "학과": mask = df['참석자_텍스트'].str.contains(sk)
        elif st_type == "주제": mask = df['주제'].str.contains(sk)
        elif st_type == "내용": mask = df['내용'].str.contains(sk)
        
        res = df[mask].sort_values(by="날짜", ascending=False)
        st.write(f"결과: {len(res)}건")
        st.dataframe(res.drop(columns=['ID', '참석자_JSON'], errors='ignore'), hide_index=True)
    elif sk: st.warning("데이터 없음")

else:
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📝 회의록 입력", "🗂️ 회의록 관리", "🔍 회의록 검색", "👥 재직교수", "🖨️ 출력", "⚙️ 설정"])

    # 1. 입력
    with tab1:
        st.header("회의록 입력")
        c1, c2, c3 = st.columns(3)
        d_in = c1.date_input("날짜", datetime.today(), key="i_d")
        t_s = c2.time_input("시작", datetime.strptime("12:00", "%H:%M"), key="i_s")
        t_e = c3.time_input("종료", datetime.strptime("13:00", "%H:%M"), key="i_e")
        p_in = st.text_input("장소", "경기캠퍼스 인문사회관 210호", key="i_p")
        tp_in = st.text_input("주제", key="i_t")
        sel_fac = st.multiselect("참석자", faculty_options, key="i_f")
        
        with st.expander("외부 인원 추가"):
            cm1, cm2, cm3 = st.columns(3)
            mn = cm1.text_input("이름", key="mn")
            md = cm2.text_input("학과", key="md")
            mr = cm3.text_input("직급", key="mr")
            mc = st.checkbox("포함", key="mc")
        
        ki = st.text_area("키워드(AI)", key="ki")
        if st.button("✨ AI 초안", key="b_ai"):
            with st.spinner("작성중..."):
                res = generate_ai_minutes(tp_in, ki)
                st.session_state['final_content'] = res
                st.rerun()
        
        fc = st.text_area("최종 내용", height=250, key="final_content")
        st.markdown("---")

        if st.session_state['save_step'] == 'input':
            if st.button("저장", type="primary", key="b_sv"):
                is_empty = (not sel_fac) and (not (mc and mn))
                cur = st.session_state.get('final_content', '')
                if not tp_in.strip() or not p_in.strip() or not cur.strip() or is_empty:
                    st.error("모든 항목을 입력하세요.")
                else:
                    att_s, att_t = [], []
                    for it in sel_fac:
                        try:
                            nm, inf = it.split(' (')[0], it.split(' (')[1][:-1]
                            dp, rk = inf.split('/')
                            att_s.append({"이름": nm, "학과": dp, "직급": rk})
                            att_t.append(f"{nm}({dp})")
                        except: pass
                    if mc and mn:
                        att_s.append({"이름": mn, "학과": md, "직급": mr})
                        att_t.append(f"{mn}({md})")
                    
                    df = load_data("회의록")
                    nxt = len(df) + 1 if not df.empty else 1
                    date_str = d_in.strftime("%Y-%m-%d")
                    
                    row = [
                        datetime.now().strftime("%Y%m%d%H%M%S"), nxt, date_str,
                        f"{t_s.strftime('%H:%M')} ~ {t_e.strftime('%H:%M')}", p_in, tp_in,
                        ", ".join(att_t), json.dumps(att_s, ensure_ascii=False), cur, ki
                    ]
                    st.session_state['temp_data'] = row
                    
                    is_dup = False
                    if not df.empty and date_str in df['날짜'].astype(str).values: is_dup = True
                    st.session_state['save_step'] = 'check_dup' if is_dup else 'confirm'
                    st.rerun()

        elif st.session_state['save_step'] == 'check_dup':
            st.warning("같은 날짜 데이터가 있습니다.")
            c1, c2, c3 = st.columns(3)
            if c1.button("덮어쓰기", key="b_ov"):
                update_row_by_date("회의록", st.session_state['temp_data'][2], st.session_state['temp_data'])
                st.session_state['save_step'] = 'success'; st.rerun()
            if c2.button("새로 추가", key="b_nw"):
                save_row("회의록", st.session_state['temp_data'])
                st.session_state['save_step'] = 'success'; st.rerun()
            if c3.button("취소", key="b_cx"):
                st.session_state['save_step'] = 'input'; st.rerun()

        elif st.session_state['save_step'] == 'confirm':
            st.info("저장하시겠습니까?")
            c1, c2 = st.columns(2)
            if c1.button("네", key="b_y"):
                save_row("회의록", st.session_state['temp_data'])
                st.session_state['save_step'] = 'success'; st.rerun()
            if c2.button("취소", key="b_n"):
                st.session_state['save_step'] = 'input'; st.rerun()

        elif st.session_state['save_step'] == 'success':
            st.success("저장 완료!")
            st.info("입력창 초기화?")
            c1, c2 = st.columns(2)
            if c1.button("네", key="b_sy"):
                for k in ["i_t", "i_p", "ki", "final_content", "mn", "md", "mr", "mc", "i_f"]:
                    if k in st.session_state: del st.session_state[k]
                st.session_state['save_step'] = 'input'; st.rerun()
            if c2.button("아니오", key="b_sn"):
                st.session_state['save_step'] = 'input'; st.rerun()

    # 2. 관리
    with tab2:
        st.header("🗂️ 회의록 관리")
        if st.button("🔄 새로고침", key="ref_m"): st.rerun()
        df_m = load_data("회의록")
        
        if st.session_state['mng_edit_id']:
            render_meeting_edit_form(df_m, faculty_options, key_suffix="mng", current_id=st.session_state['mng_edit_id'])
        else:
            if not df_m.empty:
                df_m = df_m.sort_values(by="날짜", ascending=False)
                for idx, row in df_m.iterrows():
                    with st.expander(f"[{row['날짜']}] {row['주제']}"):
                        st.write(f"내용: {row['내용'][:50]}...")
                        c_e, c_d = st.columns([1, 1])
                        if c_e.button("✏️ 수정", key=f"e_{row['ID']}_{idx}"):
                            st.session_state['mng_edit_id'] = row['ID']; st.rerun()
                        if c_d.button("🗑️ 삭제", key=f"d_{row['ID']}_{idx}"):
                            st.session_state['del_confirm_id'] = row['ID']; st.rerun()
                        
                        if st.session_state['del_confirm_id'] == row['ID']:
                            st.warning("삭제하시겠습니까?")
                            if st.button("확인", key=f"dy_{row['ID']}"):
                                delete_row("회의록", "ID", row['ID'])
                                st.session_state['del_confirm_id'] = None
                                st.success("삭제됨"); st.rerun()
            else: st.info("데이터 없음")

    # 3. 검색
    with tab3:
        st.header("🔍 회의록 검색")
        c_s1, c_s2 = st.columns([1, 3])
        with c_s1: st_type = st.selectbox("검색 기준", ["전체", "이름", "학과", "주제", "내용"], key="search_type_adm")
        with c_s2: sk = st.text_input("검색어 입력", key="sk_a")
        
        if sk:
            df = load_data("회의록")
            if not df.empty:
                if st_type == "전체": mask = df['주제'].str.contains(sk) | df['참석자_텍스트'].str.contains(sk) | df['내용'].str.contains(sk)
                elif st_type == "이름": mask = df['참석자_텍스트'].str.contains(sk)
                elif st_type == "학과": mask = df['참석자_텍스트'].str.contains(sk)
                elif st_type == "주제": mask = df['주제'].str.contains(sk)
                elif st_type == "내용": mask = df['내용'].str.contains(sk)
                
                res = df[mask].sort_values(by="날짜", ascending=False)
                st.write(f"결과: {len(res)}건")
                st.dataframe(res.drop(columns=['ID', '참석자_JSON'], errors='ignore'), hide_index=True)
            else: st.warning("데이터 없음")

    # 4. 재직교수
    with tab4:
        c_l, c_r = st.columns([2, 1])
        with c_l: st.dataframe(faculty_df, use_container_width=True, hide_index=True)
        with c_r:
            if st.session_state['fac_edit_mode']:
                st.subheader("수정")
                try:
                    target = faculty_df[faculty_df['연번'].astype(str) == str(st.session_state['fac_edit_no'])].iloc[0]
                    fn = st.text_input("이름", target['이름'], key="fn_e")
                    fd = st.text_input("학과", target['학과'], key="fd_e")
                    fr = st.selectbox("직급", ["교수","부교수","조교수","강사"], index=["교수","부교수","조교수","강사"].index(target['직급']) if target['직급'] in ["교수","부교수","조교수","강사"] else 0, key="fr_e")
                    if st.button("저장", key="b_fe_s"):
                        update_faculty_row(target['연번'], fd, fr, fn)
                        st.session_state['fac_edit_mode'] = False; st.rerun()
                    if st.button("취소", key="b_fe_c"):
                        st.session_state['fac_edit_mode'] = False; st.rerun()
                except IndexError:
                    st.error("데이터를 찾을 수 없습니다. 새로고침 해주세요.")
                    st.session_state['fac_edit_mode'] = False
            else:
                st.subheader("관리")
                with st.expander("신규", expanded=True):
                    fn = st.text_input("이름", key="fn_n")
                    fd = st.text_input("학과", key="fd_n")
                    fr = st.selectbox("직급", ["교수","부교수","조교수","강사"], key="fr_n")
                    if st.button("추가", key="b_fa_a"):
                        if fn.strip() and fd.strip() and fr:
                            save_row("재직교수", [len(faculty_df)+1, fd, fr, fn])
                            st.success("추가됨"); st.rerun()
                        else: st.error("모든 항목을 입력해주세요.")
                with st.expander("수정/삭제"):
                    f_no = st.number_input("연번", min_value=1, step=1, key="f_no")
                    c1, c2 = st.columns(2)
                    if c1.button("수정", key="b_f_m"):
                        if not faculty_df.empty and not faculty_df[faculty_df['연번'].astype(str) == str(f_no)].empty:
                            st.session_state['fac_edit_mode'] = True
                            st.session_state['fac_edit_no'] = f_no
                            st.rerun()
                        else: st.error("해당 연번 없음")
                    if c2.button("삭제", key="b_f_d"):
                        if delete_row("재직교수", "연번", f_no):
                            st.success("삭제됨"); st.rerun()
                        else: st.error("해당 연번 없음")

    # 5. 출력
    with tab5:
        df = load_data("회의록")
        if not df.empty:
            dates = sorted(df['날짜'].unique().tolist(), reverse=True)
            sels = st.multiselect("날짜 선택", dates, key="s_d_e")
            if sels:
                rows = df[df['날짜'].isin(sels)].to_dict('records')
                rows = sorted(rows, key=lambda x: x['날짜'])
                csv_data = create_csv_export(rows).to_csv(index=False).encode('utf-8-sig')
                st.download_button("CSV", csv_data, "회의록.csv", "text/csv", key="b_c_e")
                if st.button("PDF", key="b_p_g"):
                    st.download_button("다운로드", create_signature_pdf(rows), "서명부.pdf", "application/pdf", key="b_p_d")

    # 6. 설정
    with tab6:
        st.header("⚙️ 비밀번호")
        c1, c2 = st.columns(2)
        with c1:
            pa = st.text_input("새 관리자 비번", type="password", key="pa")
            if st.button("변경", key="b_pa"):
                if pa: update_password("admin", pa); st.success("완료")
        with c2:
            pu = st.text_input("새 일반 비번", type="password", key="pu")
            if st.button("변경", key="b_pu"):
                if pu: update_password("user", pu); st.success("완료")

st.markdown("---")
st.markdown("<div style='text-align: center; color: grey;'>Developed by <b>이철현</b></div>", unsafe_allow_html=True)