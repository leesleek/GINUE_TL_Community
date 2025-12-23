import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI  # 변경됨: 클라이언트 클래스 임포트
import os
import json
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

# OpenAI 클라이언트 설정 (최신 버전 방식)
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

def load_data(tab_name):
    client = get_gsheet_client()
    sh = client.open(SHEET_NAME)
    worksheet = sh.worksheet(tab_name)
    data = worksheet.get_all_records()
    return pd.DataFrame(data)

def save_data(tab_name, row_data):
    client = get_gsheet_client()
    sh = client.open(SHEET_NAME)
    worksheet = sh.worksheet(tab_name)
    worksheet.append_row(row_data)

# ---------------------------------------------------------
# 2. 기능 함수 (AI 요약 - 프롬프트 수정됨)
# ---------------------------------------------------------

def generate_ai_minutes(topic, keywords):
    if not openai_client:
        return "OpenAI API Key가 설정되지 않았거나 클라이언트 생성에 실패했습니다."
    
    # 프롬프트 수정: 제약사항을 강력하게 명시
    prompt = f"""
    작성 요청: 아래 주제와 키워드를 바탕으로 핵심 회의 내용을 정리해줘.
    
    [입력 데이터]
    - 주제: {topic}
    - 키워드 및 메모: {keywords}
    
    [필수 제약사항 - 엄격 준수]
    1. **형식**: '1.', '2.' 같은 번호나 제목을 달지 말고, 하이픈(-)으로 시작하는 글머리 기호(bullet point)만 사용할 것.
    2. **문체**: 반드시 '~함', '~음', '~논의됨', '~하기로 함' 등의 명사형 개조식으로 끝낼 것.
    3. **제외 항목**: 
       - 회의 일시, 장소, 참석자 정보는 절대 포함하지 말 것. (이미 다른 칸에 있음)
       - '이상으로 회의를 마칩니다', '회의가 시작되었습니다' 같은 서두나 결말 인사를 절대 쓰지 말 것.
       - 텍스트 강조를 위한 볼드체(**)를 절대 사용하지 말 것.
    4. **내용**: 주제에 맞춰 논의 사항, 결정 사항, 향후 계획을 포함하여 10줄 내외로 작성할 것.
    """
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "너는 군더더기 없이 핵심 내용만 개조식으로 요약하는 행정 서기야."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3 # 창의성을 낮춰서 지시사항을 더 잘 따르게 함
        )
        content = response.choices[0].message.content
        
        # 안전장치: 혹시라도 AI가 넣은 ** 기호나 불필요한 공백 제거
        content = content.replace("**", "").strip()
        
        return content
        
    except Exception as e:
        return f"AI 생성 오류: {e}"

def create_signature_pdf(meeting_data, attendees_list):
    filename = f"서명부_{meeting_data['날짜']}.pdf"
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    
    try:
        pdfmetrics.registerFont(TTFont('Nanum', FONT_PATH))
        font_name = 'Nanum'
    except:
        font_name = 'Helvetica'
    
    # 1. 머리글
    c.setFont(font_name, 14)
    c.drawString(20 * mm, height - 30 * mm, "<교수학습방법개선 공동체 운영>") 
    
    # 2. 제목
    c.setFont(font_name, 20)
    c.drawCentredString(width / 2, height - 50 * mm, "회의참석자 서명부")
    
    # 3. 일시 및 장소
    c.setFont(font_name, 11)
    try:
        dt_obj = datetime.strptime(meeting_data['날짜'], "%Y-%m-%d")
        days = ["월", "화", "수", "목", "금", "토", "일"]
        day_str = days[dt_obj.weekday()]
        formatted_date = f"{dt_obj.year}년 {dt_obj.month}월 {dt_obj.day}일 ({day_str}요일)"
    except:
        formatted_date = meeting_data['날짜']
    
    c.drawString(25 * mm, height - 70 * mm, f"• 일시: {formatted_date} {meeting_data['시간']}")
    c.drawString(25 * mm, height - 80 * mm, f"• 장소: {meeting_data['장소']}")
    
    # 4. 표 그리기
    data = [["연번", "소속학과명", "직급", "성명", "자필서명\n(도장날인X)", "비고"]]
    
    for idx, person in enumerate(attendees_list, 1):
        data.append([
            str(idx), 
            person.get('학과', ''), 
            person.get('직급', ''), 
            person.get('이름', ''), 
            "", 
            "" 
        ])
    
    while len(data) < 13:
        data.append(["", "", "", "", "", ""])

    t = Table(data, colWidths=[15*mm, 40*mm, 30*mm, 30*mm, 50*mm, 20*mm], rowHeights=12*mm)
    t.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, -1), font_name, 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
    ]))
    
    t.wrapOn(c, width, height)
    t.drawOn(c, 20 * mm, height - 100 * mm - (len(data) * 12 * mm))
    
    c.save()
    return filename

# ---------------------------------------------------------
# 3. UI 구성
# ---------------------------------------------------------

st.title("🏫 교수학습공동체 회의록 관리 시스템")

tab1, tab2, tab3, tab4 = st.tabs(["📝 회의록 입력", "🔍 검색 및 수정", "👥 재직교수 관리", "🖨️ 결과물 출력"])

try:
    faculty_df = load_data("재직교수")
    faculty_options = [f"{row['이름']} ({row['학과']}/{row['직급']})" for index, row in faculty_df.iterrows()]
except Exception as e:
    st.error(f"구글 시트 '재직교수' 탭을 불러오지 못했습니다: {e}")
    faculty_df = pd.DataFrame(columns=["연번", "학과", "직급", "이름"])
    faculty_options = []

# 탭 1: 회의록 입력
with tab1:
    col1, col2 = st.columns(2)
    with col1:
        date_input = st.date_input("회의 일자", datetime.today())
    with col2:
        start_time = st.time_input("시작 시간", datetime.strptime("12:00", "%H:%M"))
        end_time = st.time_input("종료 시간", datetime.strptime("13:00", "%H:%M"))
    
    place_input = st.text_input("회의 장소", value="경기캠퍼스 인문사회관 210호")
    topic_input = st.text_input("회의 주제")
    
    st.markdown("#### 참석자 선택")
    selected_faculty = st.multiselect("재직 교수 명단에서 선택", faculty_options)
    
    with st.expander("외부 인원 / 명단에 없는 교수 직접 입력"):
        manual_name = st.text_input("이름")
        manual_dept = st.text_input("학과")
        manual_rank = st.text_input("직급")
        add_manual = st.checkbox("이 인원 포함")
    
    keywords_input = st.text_area("회의 내용 키워드/메모 (AI 자동생성용)", height=100, 
                                placeholder="예: 절대평가 도입 논의, 줌 수업 부작용, 다음주 휴강 결정 등")
    
    if st.button("✨ 회의 내용 AI 자동 생성"):
        with st.spinner("AI가 회의록을 작성 중입니다..."):
            generated_content = generate_ai_minutes(topic_input, keywords_input)
            st.session_state['generated_content'] = generated_content
    
    final_content = st.text_area("회의 내용 (최종 확인 및 수정)", 
                                 value=st.session_state.get('generated_content', ""), 
                                 height=300)

    if st.button("💾 회의록 저장 (제출)"):
        attendees_data = []
        for item in selected_faculty:
            try:
                name = item.split(' (')[0]
                info = item.split(' (')[1].replace(')', '')
                dept, rank = info.split('/')
                attendees_data.append({"이름": name, "학과": dept, "직급": rank})
            except:
                pass 
        
        if add_manual and manual_name:
            attendees_data.append({"이름": manual_name, "학과": manual_dept, "직급": manual_rank})
            
        attendees_json = json.dumps(attendees_data, ensure_ascii=False)
        time_str = f"{start_time.strftime('%H:%M')} ~ {end_time.strftime('%H:%M')}"
        
        row = [
            datetime.now().strftime("%Y%m%d%H%M%S"),
            date_input.strftime("%Y-%m-%d"),
            time_str,
            place_input,
            topic_input,
            attendees_json,
            final_content,
            keywords_input
        ]
        
        try:
            save_data("회의록", row)
            st.success("회의록이 구글 시트에 저장되었습니다!")
        except Exception as e:
            st.error(f"저장 실패: {e}")

# 탭 2: 검색 및 수정 (조회 전용)
with tab2:
    if st.button("🔄 데이터 새로고침"):
        st.cache_data.clear()
        
    try:
        minutes_df = load_data("회의록")
        minutes_df = minutes_df.sort_values(by="날짜", ascending=False)
        search_query = st.text_input("검색 (주제, 참석자 등)")
        if search_query:
            minutes_df = minutes_df[
                minutes_df['주제'].str.contains(search_query, na=False) | 
                minutes_df['참석자'].str.contains(search_query, na=False)
            ]
        st.dataframe(minutes_df)
    except Exception as e:
        st.info("회의록 데이터가 없거나 불러올 수 없습니다. (회의록 탭이 비어있을 수 있습니다.)")

# 탭 3: 재직교수 관리
with tab3:
    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.dataframe(faculty_df, use_container_width=True)
    with col_b:
        st.markdown("#### 교수 추가")
        new_name = st.text_input("이름", key="new_name")
        new_dept = st.text_input("학과", key="new_dept")
        new_rank = st.selectbox("직급", ["교수", "부교수", "조교수", "강사", "조교", "기타"], key="new_rank")
        if st.button("추가 저장"):
            new_row = [len(faculty_df) + 1, new_dept, new_rank, new_name]
            save_data("재직교수", new_row)
            st.success("추가되었습니다. (반영을 위해 새로고침 필요)")

# 탭 4: 결과물 출력
with tab4:
    st.markdown("### 📄 결과물 생성")
    try:
        minutes_df = load_data("회의록")
        dates = minutes_df['날짜'].unique().tolist()
        selected_date = st.selectbox("출력할 회의 날짜 선택", dates)
        
        if selected_date:
            target_rows = minutes_df[minutes_df['날짜'] == selected_date]
            if not target_rows.empty:
                target_row = target_rows.iloc[0]
                st.write(f"**주제:** {target_row['주제']}")
                
                col_csv, col_pdf = st.columns(2)
                with col_csv:
                    csv_data = target_rows.to_csv(index=False).encode('utf-8-sig')
                    st.download_button("📥 회의록 CSV 다운로드", csv_data, f"회의록_{selected_date}.csv", "text/csv")
                with col_pdf:
                    if st.button("🖨️ 서명부 PDF 생성"):
                        try:
                            attendees_list = json.loads(target_row['참석자'])
                        except:
                            attendees_list = []
                        pdf_file = create_signature_pdf(target_row, attendees_list)
                        with open(pdf_file, "rb") as f:
                            st.download_button("📥 서명부 PDF 다운로드", f, pdf_file, "application/pdf")
    except:
        st.info("데이터가 없습니다.")