import streamlit as st
import pandas as pd
import requests
import json
import re
from typing import Any

# --- CONFIG ---
LLAMA_URL = "http://localhost:8080"
MODEL_NAME = "Qwen3-30B-A3B-Instruct"

# --- SESSION STATE ---
if 'history' not in st.session_state:
    st.session_state.history = []
if 'df' not in st.session_state:
    st.session_state.df = None
if 'data_loaded' not in st.session_state:
    st.session_state.data_loaded = False

# --- FUNCTIONS ---
def load_data(file):
    """
    Load data from CSV or Excel file.
    Supports: .csv, .xlsx
    """
    try:
        if file.name.endswith('.csv'):
            df = pd.read_csv(file)
            st.session_state.data_type = "csv"
        elif file.name.endswith('.xlsx'):
            df = pd.read_excel(file)
            st.session_state.data_type = "excel"
        else:
            st.error("❌ Unsupported file type. Please upload a CSV or Excel (.xlsx) file.")
            return None

        st.session_state.df = df
        st.session_state.data_loaded = True
        st.success(f"✅ Loaded {len(df)} rows from {file.name}")
        return df

    except Exception as e:
        st.error(f"❌ Error loading file: {e}")
        return None

def call_llama(prompt: str) -> str:
    try:
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": "You are a helpful Data Analyst Agent."},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "temperature": 0.3,
            "max_tokens": 1024
        }
        response = requests.post(LLAMA_URL + "/v1/chat/completions", headers=headers, data=json.dumps(payload))
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return f"Error: {response.status_code} - {response.text}"
    except Exception as e:
        return f"Exception: {e}"

# --- STREAMLIT UI ---
st.set_page_config(page_title="📊 Data Analyst Agent", layout="wide")
st.title("🤖 Data Analyst Agent with Local LLM")

# Sidebar
st.sidebar.header("📁 Upload Data")
uploaded_file = st.sidebar.file_uploader("Choose a CSV or Excel file", type=["csv", "xlsx"])

if uploaded_file:
    df = load_data(uploaded_file)
    if df is not None:
        st.session_state.df = df
        st.session_state.data_loaded = True
else:
    st.sidebar.info("Upload a CSV or Excel file to get started.")

# Main Chat Interface
if 'data_loaded' in st.session_state and st.session_state.data_loaded:
    st.markdown("### 💬 Ask a question about your data:")
    user_question = st.text_input("Type your question:", key="input")

    if user_question:
        # Add to history
        st.session_state.history.append({"role": "user", "content": user_question})

        # Show loading
        with st.spinner("🧠 Thinking..."):
            if 'df' in st.session_state:
                data_preview = st.session_state.df.head(3).to_csv(index=False)
                prompt = f"""
You are a Data Analyst Agent. You have access to the following dataset:

{data_preview}

User Question: {user_question}

Instructions:
1. Generate valid Python code using pandas and Plotly (not Matplotlib).
2. Use `import plotly.express as px`.
3. Create a figure variable: `fig = px.bar(...)`, `fig = px.line(...)`, etc.
4. Return only:
   - Answer: <description of the chart>
   - Code: <the Python code>
5. Do not use `fig.show()` — Streamlit will render it automatically.
6. Example:
   - Answer: A bar chart showing sales by product.
   - Code: import plotly.express as px
fig = px.bar(df, x='Product', y='Sales', title='Sales by Product')
"""
                response = call_llama(prompt)
            else:
                response = "Please upload a CSV or Excel file first."

        # Parse response
        answer_match = re.search(r"- Answer: (.+)", response, re.DOTALL)
        answer = answer_match.group(1).strip() if answer_match else response

        code_match = re.search(r"- Code: ([\s\S]+)", response, re.DOTALL)
        code = code_match.group(1).strip() if code_match else "No code generated."

        # Display
        st.markdown(f"**💬 Question:** {user_question}")
        st.markdown(f"**✅ Answer:** {answer}")
        st.markdown(f"**🔧 Code Used:**")
        st.code(code, language="python")

        # Handle Plotly visualization (Critical Fix)
        if code and ("import plotly" in code or "px." in code or "plotly.express" in code):
            try:
                st.markdown("**📈 Visualization:**")
                st.code(code, language="python")

                # Safe execution environment
                execution_globals = {
                    "pd": pd,
                    "df": st.session_state.df,
                    "px": __import__("plotly.express"),
                    "fig": None,
                    "__builtins__": {},
                }

                # Execute code
                exec(code, {"__builtins__": {}}, execution_globals)

                # Get fig from execution_globals
                if 'fig' in execution_globals and execution_globals['fig'] is not None:
                    st.plotly_chart(execution_globals['fig'], use_container_width=True)
                else:
                    st.warning("⚠️ No figure (fig) was created. Check the code.")
            except Exception as e:
                st.error(f"❌ Error rendering chart: {e}")
        else:
            # Show result if not a chart
            pass  # No result to show here

        # Add to history
        st.session_state.history.append({"role": "assistant", "content": response})
else:
    st.info("Upload a CSV or Excel file to start analyzing your data.")

# Show chat history
if st.session_state.history:
    with st.expander("💬 Chat History"):
        for msg in st.session_state.history:
            st.write(f"**{msg['role'].title()}:** {msg['content']}")
