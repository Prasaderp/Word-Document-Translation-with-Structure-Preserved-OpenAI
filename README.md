## Document Translator - Setup (Windows)

### Prerequisites
- Python 3.10+ installed
- An OpenAI API key

### Install
1) Open PowerShell and go to the project folder:
```powershell
cd 
```
2) Create and activate a virtual environment:
```powershell
python -m venv venv
./venv/Scripts/activate
```
3) Upgrade pip and install dependencies:
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```
4) Download the spaCy model (recommended):
```powershell
python -m spacy download en_core_web_sm
```
5) Create a .env file in the project folder with your API key:
```text
OPENAI_API_KEY=your_openai_api_key_here
```

### Run
```powershell
uvicorn fastapi_app:app --host 0.0.0.0 --port 8000 --reload
```
After it starts, open the URL shown in the terminal. The default url is: 0.0.0.0:8000



