from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from universal_bank_parser import UniversalBankParser
import os
import shutil
import tempfile

app = FastAPI(title="VA Universal Bank Parser API", description="High-performance spatial PDF & Spreadsheet parser for Indian Bank statements")

# Critical: Allows the WordPress frontend (browser) to send uploads directly to this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this to ['https://virtualauditor.in']
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

# Initialize our custom Math-Healing Spatial Parser
parser = UniversalBankParser(x_tolerance_percentage=0.15, y_tolerance_pts=5.0)

@app.post("/parse")
async def parse_statement(file: UploadFile = File(...)):
    """
    Ingests a PDF/Excel/CSV file, passes it through the UniversalBankParser, 
    and returns a clean JSON array of transactions.
    """
    if not file.filename:
        return JSONResponse(status_code=400, content={"success": False, "error": "No file uploaded"})
        
    ext = file.filename.split('.')[-1].lower()
    if ext not in ['pdf', 'xlsx', 'xls', 'csv']:
        return JSONResponse(status_code=400, content={"success": False, "error": f"Unsupported format: .{ext}"})
        
    # Spool file to temporary disk space (needed for pdfplumber & pandas)
    fd, temp_path = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # 1. Parse using Intelligence Engine
        if ext == 'pdf':
            df = parser.parse_pdf(temp_path)
        else:
            df = parser.parse_spreadsheet(temp_path)
            
        # 2. Format gracefully for the JS Client 
        # (Handling NaNs and converting numeric types safely)
        df.fillna("", inplace=True)
        records = df.to_dict(orient="records")
        
        return {"success": True, "count": len(records), "data": records}
        
    except Exception as e:
        print(f"Server Error during parse: {str(e)}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})
        
    finally:
        # 3. Always clean up temp files immediately to prevent disk bloating
        if os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    # Local dev server command
    uvicorn.run(app, host="0.0.0.0", port=8000)
