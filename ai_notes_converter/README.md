# PDF to Quarto Converter

A Streamlit application that converts handwritten PDF notes to Quarto documents using Claude Sonnet's vision API.

## Installation

### Step 1: Install Anaconda

Download and install Anaconda from [https://www.anaconda.com/download](https://www.anaconda.com/download)

**Linux:**
```bash
# Download the installer (replace with latest version)
wget https://repo.anaconda.com/archive/Anaconda3-latest-Linux-x86_64.sh

# Run the installer
bash Anaconda3-latest-Linux-x86_64.sh

# Follow the prompts and accept the license
# When asked "Do you wish to update your shell profile to automatically initialize conda?" answer yes
```

**macOS:**
```bash
# Download the installer (replace with latest version)
wget https://repo.anaconda.com/archive/Anaconda3-latest-MacOSX-x86_64.sh

# Run the installer
bash Anaconda3-latest-MacOSX-x86_64.sh

# Follow the prompts and accept the license
```

**Windows:**
- Download the installer from the Anaconda website
- Run the `.exe` file
- Check "Add Anaconda to my PATH environment variable" during installation

### Step 2: Add Anaconda to PATH (if not done during installation)

**Linux/macOS:**
```bash
# Add to your shell profile (~/.bashrc, ~/.zshrc, etc.)
export PATH="$HOME/anaconda3/bin:$PATH"

# Reload your shell configuration
source ~/.bashrc  # or source ~/.zshrc
```

**Windows:**
- Search for "Environment Variables" in Windows Settings
- Edit the PATH variable and add: `C:\Users\YourUsername\anaconda3\Scripts`

### Step 3: Create a Conda Environment

```bash
# Create a new environment called 'notes-converter' with Python 3.10
conda create -n notes-converter python=3.10

# Activate the environment
conda activate notes-converter
```

### Step 4: Install Python Dependencies

```bash
# Navigate to the project directory
cd /path/to/ai_notes_converter

# Install requirements using pip
pip install -r requirements.txt
```

### Step 5: Configure Claude API Key

1. Create a `.env` file in the project directory:
```bash
touch .env
```

2. Open the `.env` file and add your Claude API key:
```
CLAUDE_API_KEY=your_api_key_here
```

Replace `your_api_key_here` with your actual Claude API key from [https://console.anthropic.com/](https://console.anthropic.com/)

### Step 6: Install System Dependencies

**Ubuntu/Debian:**
```bash
sudo apt-get install poppler-utils
```

**macOS:**
```bash
brew install poppler
```

**Windows:**
- Download poppler from [https://github.com/oschwartz10612/poppler-windows/releases/](https://github.com/oschwartz10612/poppler-windows/releases/)
- Extract and add the `bin` folder to your PATH

### Step 7: Install Quarto (Optional - for PDF/Word export)

Download and install Quarto from [https://quarto.org/docs/get-started/](https://quarto.org/docs/get-started/)

Verify installation:
```bash
quarto --version
```

## Running the Application

1. Activate the conda environment:
```bash
conda activate notes-converter
```

2. Navigate to the project directory:
```bash
cd /path/to/ai_notes_converter
```

3. Launch the Streamlit app:
```bash
streamlit run notes_app.py
```

4. Open your browser to [http://localhost:8501](http://localhost:8501)

5. Upload your PDF file and click "Convert to Quarto"

6. Download your converted document in your preferred format

## Quick Start Summary

```bash
# 1. Create and activate environment
conda create -n notes-converter python=3.10
conda activate notes-converter

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure API key (create .env file with CLAUDE_API_KEY)

# 4. Launch app
streamlit run notes_app.py
```
