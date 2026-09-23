from __future__ import annotations
import csv, io, json, os, re, time, imaplib
from collections import defaultdict, deque
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from typing import Any
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel, Field

APP_ACCESS_PASSWORD=os.getenv("APP_ACCESS_PASSWORD","").strip()
MAX_EMAILS=200
RATE_LIMIT=30
PROVIDERS={"gmail":("imap.gmail.com",993),"outlook":("outlook.office365.com",993),"yahoo":("imap.mail.yahoo.com",993),"icloud":("imap.mail.me.com",993)}
FIELDS={"from":"From","sender":"Sender","subject":"Subject","to":"To","cc":"Cc","date":"Date","message_id":"Message-ID","return_path":"Return-Path","content_type":"Content-Type","reply_to":"Reply-To","client_ip":"Client-IP","received":"Received","authentication_results":"Authentication-Results","dkim":"DKIM","spf":"SPF","dmarc":"DMARC"}
BUCKETS=defaultdict(deque)
app=FastAPI(title="Mail Header Extractor V4",version="4.0.0")

class Mailbox(BaseModel):
    email:str; password:str; provider:str="auto"; host:str|None=None; port:int=Field(993,ge=1,le=65535); ssl:bool=True
class ExtractRequest(Mailbox):
    folder:str="INBOX"; limit:int=Field(50,ge=1,le=MAX_EMAILS); fields:list[str]=Field(default_factory=lambda:list(FIELDS)); newest_first:bool=True
class ExportRequest(BaseModel):
    format:str; fields:list[str]; rows:list[dict[str,Any]]

def auth_guard(p):
    if APP_ACCESS_PASSWORD and p!=APP_ACCESS_PASSWORD: raise HTTPException(401,"Invalid app access password.")
def limit_request(request):
    k=request.client.host if request.client else "unknown"; now=time.time(); b=BUCKETS[k]
    while b and b[0]<now-60:b.popleft()
    if len(b)>=RATE_LIMIT: raise HTTPException(429,"Too many requests. Try again later.")
    b.append(now)
@app.middleware("http")
async def mw(request,call_next):
    limit_request(request); r=await call_next(request); r.headers["Cache-Control"]="no-store"; return r

def detect(email):
    d=email.rsplit("@",1)[-1].lower() if "@" in email else ""
    if d in {"gmail.com","googlemail.com"}:return "gmail"
    if d in {"outlook.com","hotmail.com","live.com","msn.com","office365.com"}:return "outlook"
    if d in {"yahoo.com","yahoo.fr","yahoo.co.uk","ymail.com"}:return "yahoo"
    if d in {"icloud.com","me.com","mac.com"}:return "icloud"
    return "custom"
def resolve(m):
    p=m.provider.lower()
    if p=="auto":p=detect(m.email)
    if m.host:return p,m.host,m.port
    if p in PROVIDERS:return p,*PROVIDERS[p]
    raise HTTPException(400,"Unknown IMAP server. Select a provider or enter a custom IMAP host.")
def connect(m):
    _,host,port=resolve(m)
    try:
        c=imaplib.IMAP4_SSL(host,port,timeout=20) if m.ssl else imaplib.IMAP4(host,port,timeout=20); c.login(m.email,m.password); return c
    except Exception as e: raise HTTPException(400,f"IMAP connection failed: {e}")
def dec(v):
    if not v:return ""
    out=[]
    for p,c in decode_header(v):
        if isinstance(p,bytes):
            try:out.append(p.decode(c or "utf-8",errors="replace"))
            except LookupError:out.append(p.decode("utf-8",errors="replace"))
        else:out.append(p)
    return "".join(out).strip()
def vals(msg,n):return [dec(v) for v in msg.get_all(n,[])]
def authvals(v):return {k:(re.search(rf"\b{k}\s*=\s*([^\s;]+)",v or "",re.I).group(1) if re.search(rf"\b{k}\s*=\s*([^\s;]+)",v or "",re.I) else "") for k in ("dkim","spf","dmarc")}
def clientip(auth,received):
    m=re.search(r"\bclient-ip\s*[:=]\s*(?:\[([^\]]+)\]|([0-9a-fA-F:.]+))",auth or "",re.I)
    if m:return m.group(1) or m.group(2) or ""
    ip=re.compile(r"(?<![\w:])(?:(?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?![\w:])")
    for x in received:
        m=ip.search(x)
        if m:return m.group(0)
    return ""
def parse(raw,fields):
    msg=BytesParser(policy=policy.default).parsebytes(raw); received=vals(msg,"Received"); ar=vals(msg,"Authentication-Results"); at="\n".join(ar); a=authvals(at)
    v={"from":dec(msg.get("From")),"sender":dec(msg.get("Sender")),"subject":dec(msg.get("Subject")),"to":dec(msg.get("To")),"cc":dec(msg.get("Cc")),"date":dec(msg.get("Date")),"message_id":dec(msg.get("Message-ID")),"return_path":dec(msg.get("Return-Path")),"content_type":dec(msg.get("Content-Type")),"reply_to":dec(msg.get("Reply-To")),"client_ip":clientip(at,received),"received":"\n".join(received),"authentication_results":"\n".join(ar),"dkim":a["dkim"],"spf":a["spf"],"dmarc":a["dmarc"]}
    return {f:v.get(f,"") for f in fields}
def folders(c):
    st,data=c.list()
    if st!="OK":raise RuntimeError("Could not list IMAP folders.")
    out=[]
    for item in data or []:
        if not item:continue
        line=item.decode("utf-8","replace"); q=re.findall(r'"([^"]*)"',line)
        if q:out.append(q[-1])
        else:
            p=line.split(" ",2)
            if len(p)==3:out.append(p[2].strip('"'))
    return sorted(set(out),key=lambda x:(x.upper()!="INBOX",x.lower()))

@app.get("/api/health")
def health():return {"ok":True,"version":"4.0.0","platform":"vercel"}
@app.get("/api/config")
def config():return {"app_password_required":bool(APP_ACCESS_PASSWORD),"max_emails":MAX_EMAILS,"version":"4.0.0"}
@app.post("/api/test-connection")
def test(request:Request,m:Mailbox,x_app_password:str|None=Header(default=None)):
    auth_guard(x_app_password); c=connect(m)
    try:
        p,h,port=resolve(m); return {"ok":True,"provider":p,"host":h,"port":port,"message":"Connection successful."}
    finally:
        try:c.logout()
        except:pass
@app.post("/api/folders")
def getfolders(request:Request,m:Mailbox,x_app_password:str|None=Header(default=None)):
    auth_guard(x_app_password); c=connect(m)
    try:return {"folders":folders(c)}
    finally:
        try:c.logout()
        except:pass
@app.post("/api/extract")
def extract(request:Request,payload:ExtractRequest,x_app_password:str|None=Header(default=None)):
    auth_guard(x_app_password); fields=[f for f in payload.fields if f in FIELDS]
    if not fields:raise HTTPException(400,"Select at least one field.")
    c=connect(payload)
    try:
        st,_=c.select(payload.folder,readonly=True)
        if st!="OK":raise HTTPException(400,f"Could not open folder: {payload.folder}")
        st,data=c.uid("SEARCH",None,"ALL")
        if st!="OK":raise HTTPException(400,"IMAP search failed.")
        uids=(data[0] or b"").split()
        if payload.newest_first:uids.reverse()
        uids=uids[:payload.limit]; rows=[]
        for uid in uids:
            try:
                st,fetched=c.uid("FETCH",uid,"(BODY.PEEK[HEADER])")
                if st!="OK":continue
                raw=b"".join(part[1] for part in fetched or [] if isinstance(part,tuple) and isinstance(part[1],bytes))
                if raw:rows.append(parse(raw,fields))
            except Exception:continue
        return {"status":"done","total_requested":len(uids),"extracted":len(rows),"fields":fields,"rows":rows}
    finally:
        try:c.close()
        except:pass
        try:c.logout()
        except:pass
@app.post("/api/export")
def export(request:Request,p:ExportRequest,x_app_password:str|None=Header(default=None)):
    auth_guard(x_app_password); fields=[f for f in p.fields if f in FIELDS]; fmt=p.format.lower()
    if fmt=="json":return Response(json.dumps(p.rows,ensure_ascii=False,indent=2).encode(),media_type="application/json",headers={"Content-Disposition":'attachment; filename="mail_headers.json"'})
    if fmt=="txt":
        b=io.StringIO()
        for i,row in enumerate(p.rows,1):
            b.write(f"--- Email {i} ---\n"); [b.write(f"{FIELDS[f]}: {row.get(f,'')}\n") for f in fields]; b.write("\n")
        return Response(b.getvalue().encode(),media_type="text/plain; charset=utf-8",headers={"Content-Disposition":'attachment; filename="mail_headers.txt"'})
    if fmt=="csv":
        b=io.StringIO(); w=csv.DictWriter(b,fieldnames=fields,extrasaction="ignore"); w.writerow({f:FIELDS[f] for f in fields}); w.writerows(p.rows)
        return Response(b.getvalue().encode("utf-8-sig"),media_type="text/csv",headers={"Content-Disposition":'attachment; filename="mail_headers.csv"'})
    if fmt=="xlsx":
        wb=Workbook(); ws=wb.active; ws.title="Headers"; ws.append([FIELDS[f] for f in fields])
        for row in p.rows:ws.append([row.get(f,"") for f in fields])
        for col in ws.columns:
            letter=col[0].column_letter; ws.column_dimensions[letter].width=min(60,max(12,max(len(str(c.value or "")) for c in col)+2))
        b=io.BytesIO(); wb.save(b); return Response(b.getvalue(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":'attachment; filename="mail_headers.xlsx"'})
    raise HTTPException(400,"Unsupported export format.")
