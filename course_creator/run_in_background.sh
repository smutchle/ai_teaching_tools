pkill -f "streamlit run app_course.py" 2>/dev/null
sleep 1
nohup streamlit run app_course.py --server.port=9501 2>&1 &
