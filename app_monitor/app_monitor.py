import streamlit as st
import subprocess
import pandas as pd
import psutil
import re
import socket
from urllib.parse import urlparse
import time

st.set_page_config(page_title="Streamlit Process Monitor", layout="wide")

def get_streamlit_processes():
    """Find all running streamlit processes"""
    streamlit_processes = []
    
    # Run ps command to get all processes with 'streamlit run'
    try:
        ps_output = subprocess.check_output(
            ["ps", "-eo", "pid,user,start_time,cmd"], 
            universal_newlines=True
        )
        
        # Parse the output
        lines = ps_output.strip().split('\n')
        for line in lines[1:]:  # Skip header
            if "streamlit run" in line:
                parts = line.strip().split(None, 3)
                if len(parts) >= 4:
                    pid = int(parts[0])
                    user = parts[1]
                    start_time = parts[2]
                    cmd = parts[3]
                    
                    # Get process info
                    try:
                        process = psutil.Process(pid)
                        memory_usage = process.memory_info().rss / (1024 * 1024)  # MB
                        cpu_percent = process.cpu_percent(interval=0.1)
                        
                        # Extract the port from the connections
                        port = None
                        try:
                            connections = process.connections()
                            for conn in connections:
                                if conn.status == 'LISTEN' and conn.laddr.port > 1000:
                                    port = conn.laddr.port
                                    break
                        except (psutil.AccessDenied, psutil.ZombieProcess):
                            pass
                        
                        # Get the script name from the command
                        match = re.search(r'streamlit run\s+([^\s]+)', cmd)
                        script_name = match.group(1) if match else "Unknown"
                        
                        streamlit_processes.append({
                            'pid': pid,
                            'user': user,
                            'start_time': start_time,
                            'script': script_name,
                            'port': port,
                            'memory_mb': round(memory_usage, 2),
                            'cpu_percent': round(cpu_percent, 2),
                            'title': None  # Will be populated later
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
    except subprocess.CalledProcessError:
        st.error("Failed to get process list")
    
    return streamlit_processes

# Title retrieval via HTTP has been removed as requested

def kill_process(pid):
    """Kill a process given its PID"""
    try:
        process = psutil.Process(pid)
        process.terminate()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

# App title
st.title("Streamlit Process Monitor")
st.write("This app displays all running Streamlit processes and allows you to terminate them.")

# Auto-refresh option
auto_refresh = st.sidebar.checkbox("Auto-refresh", value=True)
refresh_interval = st.sidebar.slider("Refresh interval (seconds)", 5, 60, 10)

# Manual refresh button
refresh_button = st.sidebar.button("Refresh Now")

# Get processes
if auto_refresh or refresh_button or 'processes' not in st.session_state:
    with st.spinner("Scanning for Streamlit processes..."):
        processes = get_streamlit_processes()
        
        # Get the system's hostname for proper URLs
        # hostname = socket.gethostname()
        hostname = "ads1.datasci.vt.edu"
        
        # Set URLs for processes with ports (no HTTP title retrieval)
        for process in processes:
            if process['port']:
                process['title'] = f"Streamlit on port {process['port']}"
                process['url'] = f"http://{hostname}:{process['port']}"
            else:
                process['title'] = "Unknown (Port not detected)"
                process['url'] = None
        
        st.session_state.processes = processes
        st.session_state.last_refresh = time.time()

# Display processes
if 'processes' in st.session_state:
    if not st.session_state.processes:
        st.info("No Streamlit processes found")
    else:
        # Convert to DataFrame for display
        df = pd.DataFrame(st.session_state.processes)
        
        # Display last refresh time
        st.caption(f"Last refreshed: {time.strftime('%H:%M:%S', time.localtime(st.session_state.last_refresh))}")
        
        # Show count
        st.write(f"Found {len(st.session_state.processes)} Streamlit processes running")
        
        # Create columns for the table and action buttons
        col1, col2 = st.columns([3, 1])
        
        with col1:
            # Display table
            st.dataframe(
                df[['pid', 'user', 'port', 'title', 'script', 'memory_mb', 'cpu_percent', 'start_time']],
                column_config={
                    'pid': 'PID',
                    'user': 'User',
                    'port': 'Port',
                    'title': 'App Title',
                    'script': 'Script',
                    'memory_mb': st.column_config.NumberColumn('Memory (MB)', format="%.2f MB"),
                    'cpu_percent': st.column_config.NumberColumn('CPU %', format="%.1f%%"),
                    'start_time': 'Start Time'
                },
                hide_index=True
            )
        
        # Process termination section
        st.subheader("Terminate Process")
        
        col1, col2 = st.columns([1, 3])
        
        with col1:
            selected_pid = st.selectbox(
                "Select PID to terminate:",
                options=[p['pid'] for p in st.session_state.processes],
                format_func=lambda pid: f"PID {pid} - {next((p['script'] for p in st.session_state.processes if p['pid'] == pid), 'Unknown')} - {next((p['title'] for p in st.session_state.processes if p['pid'] == pid), 'Unknown')}"
            )
        
        with col2:
            process_info = next((p for p in st.session_state.processes if p['pid'] == selected_pid), None)
            if process_info:
                st.write(f"Script: {process_info['script']}")
                st.write(f"Title: {process_info['title']}")
                if process_info['url']:
                    st.write(f"URL: {process_info['url']}")
        
        # Terminate button with confirmation
        if st.button("🛑 Terminate Selected Process", type="primary"):
            confirm = st.checkbox("I confirm I want to terminate this process")
            if confirm:
                if kill_process(selected_pid):
                    st.success(f"Process {selected_pid} terminated successfully!")
                    # Remove from the session state
                    st.session_state.processes = [p for p in st.session_state.processes if p['pid'] != selected_pid]
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(f"Failed to terminate process {selected_pid}")
            else:
                st.warning("Please confirm termination by checking the box")

# Add links to open each app
st.subheader("Quick Links")
for process in st.session_state.get('processes', []):
    if process['url']:
        st.markdown(f"[{process['script']} - {process['title']} (PID: {process['pid']})]({process['url']})")

# Auto-refresh logic
if auto_refresh:
    st.empty()
    # Add JavaScript to auto-refresh the page
    st.markdown(
        f"""
        <script>
            setTimeout(function(){{
                window.location.reload();
            }}, {refresh_interval * 1000});
        </script>
        """,
        unsafe_allow_html=True
    )