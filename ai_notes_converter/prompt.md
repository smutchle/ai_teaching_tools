Create a streamlit application that allows users to upload multi-page PDF handwritten notes.  The uploads should be converted to Quarto .qmd using Claude Sonnet.  
The CLAUDE_API_KEY is in .env.  The user should be able to download Quarto .qmd file, PDF (rendered from Quarto), Word (rendered from Quarto) or a LaTeX file (also generated from Quarto).
The user should be able to check a checkbox that says "Make Accessible" that performs the necessary changes to make the Quarto (and other documents) accessible for screen readers.  

sample_notes_input.pdf is a file that can be used for testing. Use the first 2 pages for testing.

Ask clarifying questions. 