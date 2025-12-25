import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
import os
import json
import io
import numpy as np # 타입 체크용

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

# 한글 폰트 설정 (PDF용)
FONT_PATH = "NanumGothic.ttf"
if not os.path.exists(FONT_PATH):
    st.error(f"⚠️ '{FONT_PATH}' 폰트 파일이 없습니다. PDF 생성 시 한글이 깨질 수 있습니다.")

# OpenAI 클라이언트 설정
openai_client = None
if "openai" in st.secrets:
    try:
        openai_client = OpenAI(api_key=st.secrets["openai"]["api_key"])
    except Exception as e:
        st.error(f"OpenAI 설정 오류: {e}")

# 구글 시트 연결
SCOPE = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
SHEET_NAME = "교수학습공동체_DB" 

def get_gsheet_client():
    creds_dict = dict(st.secrets["connections"]["gsheets"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
    client = gspread.authorize(creds)
    return client

def get_worksheet(tab_name):
    """워크시트를 가져오되 없으면 생성"""
    client = get_gsheet_client()
    sh = client.open(SHEET_NAME)
    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        # 재직교수 탭이면 헤더 자동 생성
        ws = sh.add_worksheet(title=tab_name, rows=100, cols=10)
        if tab_name == "재직교수":
            ws.append_row(["연번", "학과", "직급", "이름"])
    return ws

def get_sheet_url():
    try:
        client = get_gsheet_client()
        sh = client.open(SHEET_NAME)
        return sh.url
    except:
        return None

def init_settings_sheet():
    ws = get_worksheet("설정")
    headers = ws.row_values(1)
    if not headers or headers != ["Key", "Value"]:
        ws.clear() 
        ws.append_row(["Key", "Value"])
        ws.append_row(["admin_pw", DEFAULT_PW["admin"]])
        ws.append_row(["user_pw", DEFAULT_PW["user"]])

def load_data(tab_name):
    try:
        ws = get_worksheet(tab_name)
        data = ws.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        return pd.DataFrame()

def save_row(tab_name, row_data):
    ws = get_worksheet(tab_name)
    # [수정] 저장 전 numpy 타입(int64)을 Python native type으로 변환
    cleaned_data = [int(x) if isinstance(x, (np.integer, np.int64)) else x for x in row_data]
    ws.append_row(cleaned_data)

def delete_row(tab_name, id_col_name, target_id):
    ws = get_worksheet(tab_name)
    cell = ws.find(str(target_id))
    if cell:
        ws.delete_rows(cell.row)
        return True
    return False

def update_row_by_id(tab_name, target_id, new_data_list):
    """ID(1열)를 기준으로 전체 행 업데이트"""
    try:
        ws = get_worksheet(tab_name)
        cell = ws.find(str(target_id), in_column=1) 
        if cell:
            # [수정] numpy int64 오류 방지 변환
            cleaned_data = [int(x) if isinstance(x, (np.integer, np.int64)) else x for x in new_data_list]
            
            end_col_char = chr(64 + len(cleaned_data))
            cell_range = f"A{cell.row}:{end_col_char}{cell.row}"
            ws.update(range_name=cell_range, values=[cleaned_data])
            return True, "성공"
        return False, "ID를 찾을 수 없습니다."
    except Exception as e:
        return False, str(e)

def update_faculty_row(target_no, new_dept, new_rank, new_name):
    """재직교수 정보 수정 (연번 기준)"""
    try:
        ws = get_worksheet("재직교수")
        cell = ws.find(str(target_no), in_column=1)
        if cell:
            ws.update_cell(cell.row, 2, new_dept)
            ws.update_cell(cell.row, 3, new_rank)
            ws.update_cell(cell.row, 4, new_name)
            return True
        return False
    except:
        return False

def update_row_by_date(tab_name, target_date, new_data_list):
    try:
        ws = get_worksheet(tab_name)
        cell = ws.find(target_date, in_column=3) 
        if cell:
            # [수정] numpy int64 오류 방지
            cleaned_data = [int(x) if isinstance(x, (np.integer, np.int64)) else x for x in new_data_list]
            
            end_col_char = chr(64 + len(cleaned_data))
            cell_range = f"A{cell.row}:{end_col_char}{cell.row}"
            ws.update(range_name=cell_range, values=[cleaned_data])
            return True
        return False
    except:
        return False

# ---------------------------------------------------------
# 2. 인증 및 비밀번호 관리 함수
# ---------------------------------------------------------
DEFAULT_PW = {"admin": "삼막로155", "user": "2601"}

def get_passwords():
    init_settings_sheet() 
    df = load_data("설정")
    pw_dict = DEFAULT_PW.copy()
    for idx, row in df.iterrows():
        if row['Key'] == 'admin_pw':
            pw_dict['admin'] = str(row['Value'])
        elif row['Key'] == 'user_pw':
            pw_dict['user'] = str(row['Value'])
    return pw_dict

def update_password(role, new_pw):
    ws = get_worksheet("설정")
    key_name = f"{role}_pw"
    cell = ws.find(key_name)
    if cell:
        ws.update_cell(cell.row, cell.col + 1, new_pw)
    else:
        ws.append_row([key_name, new_pw])
    st.cache_data.clear()

# ---------------------------------------------------------
# 3. 로직 함수 (AI, PDF, CSV)
# ---------------------------------------------------------

def generate_ai_minutes(topic, keywords):
    if not openai_client:
        return "OpenAI API Key가 설정되지 않았습니다."
    
    prompt = f"""
    작성 요청: 아래 주제와 키워드를 바탕으로 핵심 회의 내용을 정리해줘.
    
    [입력 데이터]
    - 주제: {topic}
    - 키워드 및 메모: {keywords}
    
    [필수 제약사항 - 엄격 준수]
    1. **형식**: 번호(1., 2.)나 제목 없이, 하이픈(-)으로 시작하는 글머리 기호만 사용할 것.
    2. **문체**: 반드시 '~함', '~음', '~논의됨', '~하기로 함' 등의 명사형 개조식으로 끝낼 것. '해요체' 절대 사용 금지.
    3. **제외**: 일시, 장소, 참석자 정보 포함 금지. 인사말(시작/끝) 포함 금지.
    4. **강조**: 볼드체(**) 사용 금지.
    5. **분량**: 10줄 내외.
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini", 
            messages=[{"role": "system", "content": "너는 대학 행정 회의록 전문 서기야."}, {"role": "user", "content": prompt}],
            temperature=0.3
        )
        content = response.choices[0].message.content
        return content.replace("**", "").strip()
    except Exception as e:
        return f"AI 생성 오류: {e}"

def create_signature_pdf(meeting_rows):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    try:
        pdfmetrics.registerFont(TTFont('Nanum', FONT_PATH))
        font_name = 'Nanum'
    except:
        font_name = 'Helvetica'

    for i, meeting in enumerate(meeting_rows):
        if i > 0: c.showPage()
        
        try:
            dt = datetime.strptime(meeting['날짜'], "%Y-%m-%d")
            days = ["월", "화", "수", "목", "금", "토", "일"]
            day_str = days[dt.weekday()]
            
            # 시간 포맷 (DB: 12:00 ~ 13:00) -> PDF용
            time_parts = meeting['시간'].split('~')
            start_t = time_parts[0].strip().replace(":", "시 ") + "분"
            end_t = time_parts[1].strip().replace(":", "시 ") + "분"
            
            full_date_str = f"{dt.year}년 {dt.month}월 {dt.day}일({day_str}요일) {start_t} - {end_t}"
        except:
            full_date_str = f"{meeting['날짜']} {meeting['시간']}"

        c.setFont(font_name, 14)
        c.drawString(20 * mm, height - 25 * mm, "<교수학습방법개선 공동체 운영>") 
        c.setFont(font_name, 20)
        c.drawCentredString(width / 2, height - 45 * mm, "회의참석자 서명부")
        c.setFont(font_name, 11)
        c.drawString(25 * mm, height - 65 * mm, f"■ 일시: {full_date_str}")
        c.drawString(25 * mm, height - 73 * mm, f"■ 장소: {meeting['장소']}")

        table_data = [["연번", "소속학과명", "직급", "성명", "자필서명\n(도장날인X)", "비고"]]
        attendees = []
        try:
            attendees = json.loads(meeting['참석자_JSON'])
        except: pass

        for idx, person in enumerate(attendees, 1):
            table_data.append([str(idx), person.get('학과', ''), person.get('직급', ''), person.get('이름', ''), "", ""])
        while len(table_data) < 11:
             table_data.append(["", "", "", "", "", ""])

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
            
            # [수정] CSV 시간 포맷 요구사항 반영 (12:30 ~ 14:15)
            # DB에는 "12:30 ~ 14:15" 형태로 이미 저장되어 있음.
            # 하지만 혹시 모르니 공백 등 정리
            time_str = meeting['시간'].replace(" ", "") # 공백제거 후
            time_str = time_str.replace("~", " ~ ")   # 보기 좋게 띄어쓰기
            
            formatted_date = f"{short_year}.{dt.month}.{dt.day}.({days[dt.weekday()]}), {time_str}"
        except:
            formatted_date = f"{meeting['날짜']}, {meeting['시간']}"
            
        attendees_str = meeting['참석자_텍스트'].replace(", ", "\n").replace(",", "\n")
        
        export_list.append({
            "일시": formatted_date, 
            "장소": meeting['장소'], 
            "주제": meeting['주제'],
            "참석자(3명 이상)": attendees_str, 
            "회의 내용(2줄 이상, 구체적으로 작성)": meeting['내용'],
            "증빙자료": "서명부\n첨부"
        })
    return pd.DataFrame(export_list)

# ---------------------------------------------------------
# 4. 공통: 회의록 수정 폼 렌더링 함수
# ---------------------------------------------------------
def render_meeting_edit_form(df_m, faculty_options, key_suffix, current_id):
    """
    key_suffix: mng(관리), sch(검색) 등 탭 구분자
    current_id: 현재 수정 중인 ID
    """
    st.markdown("---")
    
    # 목록 돌아가기 버튼
    if st.button("⬅️ 수정 취소 및 목록으로 돌아가기", key=f"btn_top_back_{key_suffix}"):
        # [수정] 해당 탭의 수정 상태만 초기화
        if key_suffix == 'mng': st.session_state['mng_edit_id'] = None
        elif key_suffix == 'sch': st.session_state['sch_edit_id'] = None
        st.rerun()

    st.subheader(f"✏️ 회의록 수정 (ID: {current_id})")
    
    target_row = df_m[df_m['ID'].astype(str) == str(current_id)].iloc[0]
    
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

        # [수정] int casting added to prevent int64 error
        updated_row = [
            str(target_row['ID']), 
            int(target_row['연번']), # Cast to int
            e_date.strftime("%Y-%m-%d"),
            f"{e_start.strftime('%H:%M')} ~ {e_end.strftime('%H:%M')}", 
            e_place, 
            e_topic,
            final_txt, 
            final_json, 
            e_content, 
            target_row['키워드']
        ]
        
        success, msg = update_row_by_id("회의록", target_row['ID'], updated_row)
        if success:
            st.success("수정되었습니다.")
            # 해당 탭의 수정 상태 초기화
            if key_suffix == 'mng': st.session_state['mng_edit_id'] = None
            elif key_suffix == 'sch': st.session_state['sch_edit_id'] = None
            st.rerun()
        else:
            st.error(f"수정 실패: {msg}")

    if col_cancel.button("취소", key=f"btn_cc_{key_suffix}"):
        if key_suffix == 'mng': st.session_state['mng_edit_id'] = None
        elif key_suffix == 'sch': st.session_state['sch_edit_id'] = None
        st.rerun()

# ---------------------------------------------------------
# 5. 메인 UI
# ---------------------------------------------------------
if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
if 'user_role' not in st.session_state: st.session_state['user_role'] = None
if 'generated_content' not in st.session_state: st.session_state['generated_content'] = ""

if 'save_step' not in st.session_state: st.session_state['save_step'] = 'input'
if 'temp_data' not in st.session_state: st.session_state['temp_data'] = None

# [수정] 수정 상태를 탭별로 분리하여 관리
if 'mng_edit_id' not in st.session_state: st.session_state['mng_edit_id'] = None
if 'sch_edit_id' not in st.session_state: st.session_state['sch_edit_id'] = None

if 'del_confirm_id' not in st.session_state: st.session_state['del_confirm_id'] = None
if 'fac_edit_mode' not in st.session_state: st.session_state['fac_edit_mode'] = False
if 'fac_edit_no' not in st.session_state: st.session_state['fac_edit_no'] = None

# --- [A] 로그인 화면 ---
if not st.session_state['logged_in']:
    st.title("🔒 교수학습공동체 시스템 로그인")
    col_l1, col_l2 = st.columns([1, 2])
    with col_l1:
        auth_mode = st.radio("로그인 유형", ["관리자", "일반사용자"], key="login_rad")
    with col_l2:
        input_pw = st.text_input("비밀번호 입력", type="password", key="login_pw")
        if st.button("로그인", type="primary", key="btn_login"):
            current_pws = get_passwords()
            if auth_mode == "관리자":
                if input_pw == current_pws['admin']:
                    st.session_state['logged_in'] = True; st.session_state['user_role'] = "admin"; st.rerun()
                else: st.error("❌ 비밀번호 오류")
            else:
                if input_pw == current_pws['user']:
                    st.session_state['logged_in'] = True; st.session_state['user_role'] = "user"; st.rerun()
                else: st.error("❌ 비밀번호 오류")
    st.stop()

# --- [B] 메인 화면 ---
col_head1, col_head2 = st.columns([3, 1.5]) 
with col_head1: st.title("🏫 교수학습공동체 회의록 관리 시스템")
with col_head2:
    st.markdown(f"<div style='text-align: right; margin-bottom: 5px;'>👤 <b>{st.session_state['user_role']}</b> 모드</div>", unsafe_allow_html=True)
    if st.session_state['user_role'] == 'admin':
        hb1, hb2 = st.columns([1, 1])
        with hb1:
            sheet_url = get_sheet_url()
            if sheet_url: st.link_button("📂 구글 시트", sheet_url, use_container_width=True)
            else: st.button("연결 실패", disabled=True, use_container_width=True, key="btn_fail_link")
        with hb2:
            if st.button("🚪 로그아웃", use_container_width=True, key="btn_logout_adm"):
                st.session_state.clear(); st.rerun()
    else:
        if st.button("🚪 로그아웃", use_container_width=True, key="btn_logout_usr"):
            st.session_state.clear(); st.rerun()

st.divider()

# --- [C] 기능 탭 ---
faculty_df = load_data("재직교수")
faculty_options = [f"{row['이름']} ({row['학과']}/{row['직급']})" for idx, row in faculty_df.iterrows()] if not faculty_df.empty else []

if st.session_state['user_role'] == 'user':
    st.info("💡 일반사용자는 '회의록 검색' 기능만 사용할 수 있습니다.")
    st.header("🔍 회의록 검색")
    search_keyword = st.text_input("검색어 입력 (이름, 학과, 주제 등)", key="search_user_inp")
    
    if search_keyword:
        minutes_df = load_data("회의록")
        if not minutes_df.empty:
            mask = (
                minutes_df['주제'].astype(str).str.contains(search_keyword) | 
                minutes_df['참석자_텍스트'].astype(str).str.contains(search_keyword) |
                minutes_df['내용'].astype(str).str.contains(search_keyword)
            )
            results = minutes_df[mask].sort_values(by="날짜", ascending=False)
            st.write(f"검색 결과: {len(results)}건")
            for idx, row in results.iterrows():
                with st.expander(f"[{row['날짜']}] {row['주제']}"):
                    st.write(f"**일시:** {row['날짜']} {row['시간']}")
                    st.write(f"**장소:** {row['장소']}")
                    st.write(f"**참석자:** {row['참석자_텍스트']}")
                    st.text_area("내용", row['내용'], disabled=True, height=200, key=f"usr_cnt_{row['ID']}_{idx}")
        else: st.warning("데이터 없음")
else:
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📝 회의록 입력", "🗂️ 회의록 관리", "🔍 검색", "👥 재직교수", "🖨️ 출력", "⚙️ 설정"])

    # 1. 입력
    with tab1:
        st.header("회의록 신규 입력")
        c1, c2, c3 = st.columns(3)
        d_in = c1.date_input("날짜", datetime.today(), key="in_d")
        t_s = c2.time_input("시작", datetime.strptime("12:00", "%H:%M"), key="in_s")
        t_e = c3.time_input("종료", datetime.strptime("13:00", "%H:%M"), key="in_e")
        p_in = st.text_input("장소", "경기캠퍼스 인문사회관 210호", key="in_p")
        tp_in = st.text_input("주제", key="in_t")
        
        sel_fac = st.multiselect("참석자 선택", faculty_options, key="in_f")
        with st.expander("외부 인원 추가"):
            col_m1, col_m2, col_m3 = st.columns(3)
            m_nm = col_m1.text_input("이름", key="in_mn")
            m_dp = col_m2.text_input("학과", key="in_md")
            m_rk = col_m3.text_input("직급", key="in_mr")
            add_man = st.checkbox("포함", key="in_mc")

        kwd_in = st.text_area("키워드 (AI용)", key="in_k")
        if st.button("✨ AI 초안 생성", key="btn_ai_gen"):
            with st.spinner("생성 중..."):
                res = generate_ai_minutes(tp_in, kwd_in)
                st.session_state['final_content'] = res 
                st.rerun()
        
        fin_cont = st.text_area("최종 내용", height=250, key="final_content")
        st.markdown("---")

        if st.session_state['save_step'] == 'input':
            if st.button("저장 (제출)", type="primary", key="btn_sv_main"):
                is_att_empty = (not sel_fac) and (not (add_man and m_nm))
                cur_cont = st.session_state.get('final_content', '')
                if not tp_in.strip() or not p_in.strip() or not cur_cont.strip() or is_att_empty:
                    st.error("⚠️ 모든 항목 입력 필요")
                else:
                    att_struct, att_txt = [], []
                    for it in sel_fac:
                        try:
                            nm, info = it.split(' (')[0], it.split(' (')[1][:-1]
                            dp, rk = info.split('/')
                            att_struct.append({"이름": nm, "학과": dp, "직급": rk})
                            att_txt.append(f"{nm}({dp})")
                        except: pass
                    if add_man and m_nm:
                        att_struct.append({"이름": m_nm, "학과": m_dp, "직급": m_rk})
                        att_txt.append(f"{m_nm}({m_dp})")

                    mins_data = load_data("회의록")
                    nxt = len(mins_data) + 1 if not mins_data.empty else 1
                    input_date_str = d_in.strftime("%Y-%m-%d")
                    
                    row_data = [
                        datetime.now().strftime("%Y%m%d%H%M%S"), nxt, input_date_str,
                        f"{t_s.strftime('%H:%M')} ~ {t_e.strftime('%H:%M')}", p_in, tp_in,
                        ", ".join(att_txt), json.dumps(att_struct, ensure_ascii=False), cur_cont, kwd_in
                    ]
                    st.session_state['temp_data'] = row_data
                    
                    is_dup = False
                    if not mins_data.empty:
                        if input_date_str in mins_data['날짜'].astype(str).values: is_dup = True
                    
                    st.session_state['save_step'] = 'check_dup' if is_dup else 'confirm'
                    st.rerun()

        elif st.session_state['save_step'] == 'check_dup':
            st.warning(f"⚠️ {st.session_state['temp_data'][2]} 중복 데이터 발견")
            c_d1, c_d2, c_d3 = st.columns(3)
            if c_d1.button("덮어쓰기", key="btn_dup_ovwr"):
                update_row_by_date("회의록", st.session_state['temp_data'][2], st.session_state['temp_data'])
                st.session_state['save_step'] = 'success'; st.rerun()
            if c_d2.button("새로 추가", key="btn_dup_new"):
                save_row("회의록", st.session_state['temp_data'])
                st.session_state['save_step'] = 'success'; st.rerun()
            if c_d3.button("취소", key="btn_dup_cx"):
                st.session_state['save_step'] = 'input'; st.rerun()

        elif st.session_state['save_step'] == 'confirm':
            st.info("저장하시겠습니까?")
            c_c1, c_c2 = st.columns(2)
            if c_c1.button("네, 저장", key="btn_conf_yes"):
                save_row("회의록", st.session_state['temp_data'])
                st.session_state['save_step'] = 'success'; st.rerun()
            if c_c2.button("취소", key="btn_conf_no"):
                st.session_state['save_step'] = 'input'; st.rerun()

        elif st.session_state['save_step'] == 'success':
            st.success("완료!")
            st.info("입력창 초기화?")
            c_s1, c_s2 = st.columns(2)
            if c_s1.button("네", key="btn_suc_yes"):
                for k in ["in_t", "in_p", "in_k", "final_content", "in_mn", "in_f"]:
                    if k in st.session_state: del st.session_state[k]
                st.session_state['save_step'] = 'input'; st.rerun()
            if c_s2.button("아니오", key="btn_suc_no"):
                st.session_state['save_step'] = 'input'; st.rerun()

    # 2. 관리
    with tab2:
        st.header("🗂️ 회의록 관리")
        if st.button("🔄 새로고침", key="ref_tab2"): st.rerun()
        df_m = load_data("회의록")
        
        # [수정] 관리 탭 전용 state 사용 (mng_edit_id)
        if st.session_state['mng_edit_id']:
            render_meeting_edit_form(df_m, faculty_options, key_suffix="mng", current_id=st.session_state['mng_edit_id'])
        else:
            if not df_m.empty:
                df_m = df_m.sort_values(by="날짜", ascending=False)
                for idx, row in df_m.iterrows():
                    with st.expander(f"[{row['날짜']}] {row['주제']}"):
                        st.write(f"내용: {row['내용'][:50]}...")
                        c_e, c_d = st.columns([1, 1])
                        if c_e.button("✏️ 수정", key=f"e_mng_{row['ID']}_{idx}"):
                            st.session_state['mng_edit_id'] = row['ID']
                            st.rerun()
                        if c_d.button("🗑️ 삭제", key=f"d_mng_{row['ID']}_{idx}"):
                            st.session_state['del_confirm_id'] = row['ID']
                            st.rerun()
                        
                        if st.session_state['del_confirm_id'] == row['ID']:
                            st.warning("삭제 확인")
                            if st.button("확인", key=f"dy_mng_{row['ID']}"):
                                delete_row("회의록", "ID", row['ID'])
                                st.session_state['del_confirm_id'] = None
                                st.success("삭제됨"); st.rerun()
            else: st.info("데이터 없음")

    # 3. 검색
    with tab3:
        st.header("🔍 검색 및 수정")
        sk = st.text_input("검색어", key="search_adm_inp")
        
        # [수정] 검색 탭 전용 state 사용 (sch_edit_id)
        if st.session_state['sch_edit_id']:
            df_m_search = load_data("회의록")
            render_meeting_edit_form(df_m_search, faculty_options, key_suffix="sch", current_id=st.session_state['sch_edit_id'])
        else:
            if sk:
                df_s = load_data("회의록")
                if not df_s.empty:
                    res = df_s[df_s['주제'].str.contains(sk) | df_s['참석자_텍스트'].str.contains(sk) | df_s['내용'].str.contains(sk)]
                    st.dataframe(res)
                    for idx, row in res.iterrows():
                        with st.expander(f"결과: {row['주제']} ({row['날짜']})"):
                             if st.button("✏️ 수정", key=f"e_sch_{row['ID']}_{idx}"):
                                 st.session_state['sch_edit_id'] = row['ID']
                                 st.rerun()

    # 4. 재직교수
    with tab4:
        c_l, c_r = st.columns([2, 1])
        with c_l: st.dataframe(faculty_df, use_container_width=True, hide_index=True)
        with c_r:
            if st.session_state['fac_edit_mode']:
                st.subheader("수정")
                target_fac = faculty_df[faculty_df['연번'] == st.session_state['fac_edit_no']].iloc[0]
                fe_nm = st.text_input("이름", target_fac['이름'], key="fe_nm_f")
                fe_dp = st.text_input("학과", target_fac['학과'], key="fe_dp_f")
                fe_rk = st.selectbox("직급", ["교수","부교수","조교수","강사"], index=["교수","부교수","조교수","강사"].index(target_fac['직급']) if target_fac['직급'] in ["교수","부교수","조교수","강사"] else 0, key="fe_rk_f")
                if st.button("저장", key="btn_fe_sv"):
                    update_faculty_row(target_fac['연번'], fe_dp, fe_rk, fe_nm)
                    st.session_state['fac_edit_mode'] = False; st.rerun()
                if st.button("취소", key="btn_fe_cc"):
                    st.session_state['fac_edit_mode'] = False; st.rerun()
            else:
                st.subheader("관리")
                with st.expander("신규", expanded=True):
                    nn = st.text_input("이름", key="fa_n_f")
                    nd = st.text_input("학과", key="fa_d_f")
                    nr = st.selectbox("직급", ["교수","부교수","조교수","강사"], key="fa_r_f")
                    if st.button("추가", key="btn_fa_add"):
                        save_row("재직교수", [len(faculty_df)+1, nd, nr, nn])
                        st.rerun()
                with st.expander("수정/삭제"):
                    target_no = st.number_input("연번", min_value=1, step=1, key="fd_no")
                    c_fe, c_fd = st.columns(2)
                    if c_fe.button("수정", key="btn_fd_mod"):
                        if not faculty_df[faculty_df['연번'] == target_no].empty:
                            st.session_state['fac_edit_mode'] = True
                            st.session_state['fac_edit_no'] = target_no
                            st.rerun()
                    if c_fd.button("삭제", key="btn_fd_del"):
                        delete_row("재직교수", "연번", target_no); st.rerun()

    # 5. 출력
    with tab5:
        df_o = load_data("회의록")
        if not df_o.empty:
            dates = sorted(df_o['날짜'].unique().tolist(), reverse=True)
            sels = st.multiselect("날짜 선택", dates, key="sel_dates_exp")
            if sels:
                t_rows = df_o[df_o['날짜'].isin(sels)].to_dict('records')
                t_rows = sorted(t_rows, key=lambda x: x['날짜'])
                st.download_button("CSV", create_csv_export(t_rows).to_csv(index=False, encoding='utf-8-sig'), "회의록.csv", "text/csv", key="btn_csv_exp")
                if st.button("PDF", key="btn_pdf_gen"):
                    st.download_button("다운로드", create_signature_pdf(t_rows), "서명부.pdf", "application/pdf", key="btn_pdf_dl")

    # 6. 설정
    with tab6:
        st.header("⚙️ 비밀번호")
        c_p1, c_p2 = st.columns(2)
        with c_p1:
            np_a = st.text_input("새 관리자 비번", type="password", key="np_a_s")
            if st.button("변경", key="btn_cp_a"):
                if np_a: update_password("admin", np_a); st.success("완료")
        with c_p2:
            np_u = st.text_input("새 일반 비번", type="password", key="np_u_s")
            if st.button("변경", key="btn_cp_u"):
                if np_u: update_password("user", np_u); st.success("완료")

st.markdown("---")
st.markdown("<div style='text-align: center; color: grey;'>Developed by <b>이철현</b></div>", unsafe_allow_html=True)