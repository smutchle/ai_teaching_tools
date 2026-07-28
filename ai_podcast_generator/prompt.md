I want to create a streamlit app that generates Podcasts with 1 to 3 hosts.  If it's a 1 host, it's more of just an overview.  The user will upload PDF materials that are the source/context for the podcast.  It's important that the voices are realistic and engaging (not robotic). Additionally, the user can specify some direction on how to frame the material.

The outputs are: An audio file of the podcast. 5 different "catchy" titles for the content.  A short summary of the podcast (appropriate for a spotify style overview). An thumbnail image with abstract graphics using the MCP server below.  There should be a single ZIP download of all of the materials (along with the individual downloads).

Resources available:

.env with the following entries that point to an OpenAI compatable API

OPEN_AI_ENDPOINT=https://llm-api.arc.vt.edu/api/v1
OPEN_AI_API_KEY=sk-36eb5fc6ba7...82bc74f
OPEN_AI_MODEL=thinking_latest

"thinking_latest" is an alias to GLM-5.2.

There is an MCP server running on ads2.datasci.vt.edu and configured in claude code on this server (ads1.datasci.vt.edu) that can be used to generate the thumbnails.  It's called zimage.

The API https://github.com/souzatharsis/podcastfy can be used to generate the podcasts.


Direction:

The user should be able to specify the number of podcasters, style of podcast (deep dive, debate or overview), the podcast personas (include gender in name).  The user should also be able to give additional direction to the LLM that should default to "Use a light-hearted, dynamic, interactive style of discussion."

Create a new anaconda env called podcastify for the installs.  Use streamlit for the front end.  Make it a stepped process: 

0. Give the user the space to enter a different API key that overrides OPEN_AI_API_KEY
1. Upload either an existing transcript or source PDFs.
2. Have the LLM do the necessary generation for the podcast (including re-formatting existing transcript) so that it works with the generation API
3. Generate the transcript and give an editable version to update/review in a text editor style interface (if possible)
4. Once the user clicks the Generate Podcast button, it generates all the rest of the outputs.
5. Give a final download page/section.

Ask questions if anything is not clear.

