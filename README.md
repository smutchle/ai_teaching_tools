## AI Teaching Tools

There are currently 3 tools in the AI Teaching tools platform.

1. Viz Builder - Allows you (or your students) to build live, interactive visualizations using LLM prompting.
2. Course Creator - A robust tool for generating complete online curriculums using a LLM.
3. App Monitor - A tool to monitor your streamlit apps (e.g. Viz Builder and Course Creator, etc.)

### Preconfiguration

There are a couple of APIs that you can sign up for if you want commercial level LLMs (and also for Google Searching for Web Researcher (required)).

- [Recommended: Setup and Possibly Fund Google Gemini API Key](https://ai.google.dev/gemini-api/docs/api-key)
  - Record the API key
- [Optional: Setup and Fund an Anthropic Account](https://console.anthropic.com/login?returnTo=%2F%3F)
  - Record the API key
- [Optional: Setup and Fund OpenAI API Key](https://platform.openai.com/api-keys)
  - Record the API key

### Installation

0. [Install git version control software](https://git-scm.com/downloads)
1. [Install anaconda for virtual python environments](https://www.anaconda.com/download)
2. Download (or clone) the repository.

```
md ai_tools
cd ai_tools
git clone https://github.com/smutchle/ai_teaching_tools
```

3. Create your anaconda environment:

```
    conda create --name ai_teaching
    conda activate ai_teaching
```

4. Install the required libraries:

`pip install streamlit python-dotenv numpy pandas matplotlib seaborn plotly scipy scikit-learn statsmodels altair pygame`

5. In each folder, rename .env_sample to .env. Edit each .env file and put in your API key values, etc.
6. Run the appropriate .sh (Linux/Mac) or .bat (Windows) file. This will launch the respective web interface. If there is no .bat file, you can simply rename the .sh file to .bat.
