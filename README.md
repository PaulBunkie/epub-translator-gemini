 ```markdown
 # EPUB Translator (using Google Gemini)

 A Python script to translate EPUB books section by section using the Google Gemini API with caching support.

 ## Features

 *   Translates EPUB files section by section ("on demand").
 *   Caches translated sections to avoid repeated API calls.
 *   Option to save the full translated text into a single file.
 *   Option to limit the number of sections to translate (useful for testing).
 *   Option to list available Gemini models.
 *   Configurable target language and Gemini model.
 *   Uses `.epub_cache` for section cache and `.translated` for full output files.

 ## Requirements

 *   Python 3.x
 *   Required Python libraries (install via pip):
     ```bash
     pip install google-generativeai ebooklib beautifulsoup4 lxml
     ```

 ## Setup

 1.  **Clone the repository (or download the files).**
 2.  **Install dependencies:**
     ```bash
     pip install -r requirements.txt
     ```
     *(Alternatively, install manually: `pip install google-generativeai ebooklib beautifulsoup4 lxml`)*
 3.  **Set up Google Gemini API Key:**
     *   Obtain an API key from Google AI Studio: [https://aistudio.google.com/](https://aistudio.google.com/)
     *   Set the API key as an environment variable named `GOOGLE_API_KEY`.
       *   **Linux/macOS:** `export GOOGLE_API_KEY="YOUR_API_KEY"`
       *   **Windows (CMD):** `set GOOGLE_API_KEY=YOUR_API_KEY` (for current session)
       *   **Windows (PowerShell):** `$env:GOOGLE_API_KEY="YOUR_API_KEY"` (for current session)
       *   For persistent setting on Windows, use System Properties -> Environment Variables.

 ## Usage

 Run the `main_tester.py` script from your terminal.

 **Basic Translation (with caching):**
 ```bash
 python main_tester.py "path/to/your_book.epub"
 ```
 (Translates to Russian using `gemini-1.5-flash`. Translated sections are cached in `.epub_cache/`)

 **Specify Language and Model:**
 ```bash
 python main_tester.py "path/to/your_book.epub" -l english -m gemini-1.5-pro-latest
 ```

 **Translate only the first N sections:**
 ```bash
 python main_tester.py "path/to/your_book.epub" --parts 5
 ```

 **Translate and save the full text to a single file:**
 ```bash
 python main_tester.py "path/to/your_book.epub" --full
 ```
 (Saves to `.translated/your_book_russian_translated.txt`)

 **Combine options:**
 ```bash
 python main_tester.py "path/to/your_book.epub" --full --parts 10 -l german
 ```

 **List available models:**
 ```bash
 python main_tester.py --list-models
 ```
 *(Note: `input_file` argument is not needed when using `--list-models`)*

 ## Modules

 *   `main_tester.py`: Main script for running tests and orchestration.
 *   `translation_module.py`: Handles interaction with the Gemini API for translation.
 *   `epub_parser.py`: Parses EPUB files and extracts text content.
 *   `cache_manager.py`: Manages caching of translated sections.

 ## TODO / Potential Improvements

 *   More robust error handling for API calls.
 *   Improved HTML text extraction logic in `epub_parser.py`.
 *   Option to clear the cache.
 *   Progress indicator for long translations.
 *   (Add your ideas here)
 ```