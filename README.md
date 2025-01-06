# Neo-Anki: Natural Japanese Learning

A streamlit-based Japanese learning application that combines spaced repetition with natural content consumption.

## Features

- Content import from EPUB books, text, and URLs
- Smart sentence extraction and difficulty analysis
- Spaced repetition system with customizable intervals 
- Grammar and vocabulary analysis using LLM
- Text-to-speech support
- Progress tracking and statistics
- Chat interface with AI tutor
- Dark mode support

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
streamlit run app.py
```

## Project Structure

```
.
├── app.py              # Main application code
├── requirements.txt    # Python dependencies
└── data/              # Application data storage
    ├── content/       # Imported content storage
    └── backups/       # Backup files
```

## Requirements

requirements.txt:
```
streamlit
ebooklib
beautifulsoup4
gtts
plotly
ollama
requests
```

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## Planned Features

- Content recommendations
- Social sharing features
- Enhanced difficulty analysis
- Mobile support
- Multi-language support
