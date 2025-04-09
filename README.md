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

1. Download (or clone) the repository.
2. Create your anaconda environment:

```
    conda create --name ai_teaching
    conda activate ai_teaching
```

3. Install the required libraries:

`ip install streamlit python-dotenv numpy pandas matplotlib seaborn plotly scipy scikit-learn statsmodels altair pygame`

4. In each folder, rename .env_sample to .env. Edit each .env file and put in your API key values, etc.
5. Run the appropriate .sh (Linux/Mac) or .bat (Windows) file. This will launch the respective web interface. If there is no .bat file, you can simply rename the .sh file to .bat.
