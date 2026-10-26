#!/usr/bin/env python3
"""Central de Controle UESP 2027 — servidor local com controle de chegada."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import sqlite3, json, socket, io
from pathlib import Path
from email.parser import BytesParser
from email.policy import default
from datetime import datetime

HERE=Path(__file__).resolve().parent
AUDITION_DB=HERE/"banco_audicao.db"
CONTEST_DB=HERE/"banco_concurso.db"
CATEGORIES=["Rainha do Samba","Passista de Ouro","Diva do Samba (Plus Size)"]

def dbconn(database=AUDITION_DB):
    con=sqlite3.connect(database,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS candidates (
      id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, name TEXT NOT NULL,
      imported_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(category,name))""")
    con.execute("""CREATE TABLE IF NOT EXISTS arrivals (
      candidate_id INTEGER PRIMARY KEY, arrived_at TEXT NOT NULL, device TEXT DEFAULT 'Recepção',
      FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS scores (
      id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL, evaluator TEXT NOT NULL,
      communication REAL, samba REAL, updated_at TEXT NOT NULL, UNIQUE(candidate_id,evaluator),
      FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS audition_result_publication (
      candidate_id INTEGER PRIMARY KEY, category TEXT NOT NULL, position INTEGER NOT NULL,
      published_at TEXT NOT NULL, FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE)""")
    con.execute("""CREATE TABLE IF NOT EXISTS evaluator_roster (
      stage TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL,
      PRIMARY KEY(stage,name))""")
    return con

def parse_multipart(content_type,body):
    msg=BytesParser(policy=default).parsebytes(b"Content-Type: "+content_type.encode("latin-1")+b"\r\nMIME-Version: 1.0\r\n\r\n"+body)
    if not msg.is_multipart(): return None,None
    for part in msg.iter_parts():
        if "form-data" in part.get("Content-Disposition","") and part.get_param("name",header="content-disposition")=="file":
            return part.get_filename() or "candidatos.xlsx",part.get_payload(decode=True)
    return None,None


def contest_eligible(con):
    """Return all audition participants with any saved audition score data.

    The contest evaluator must not wait for the full number of finalists or for
    five complete audition evaluations. Available names and partial score data
    are enough to make a participant selectable in the contest stage.
    """
    cs=con.execute("""SELECT c.id,c.category,c.name FROM candidates c
        ORDER BY c.category,c.name COLLATE NOCASE""").fetchall()
    rs=con.execute("SELECT candidate_id,evaluator,communication,samba FROM scores").fetchall()
    by={}
    for r in rs:
        by.setdefault(r["candidate_id"],{})[r["evaluator"]]={
            "communication":r["communication"],"samba":r["samba"]}
    chosen=[]
    for c in cs:
        # Normaliza acentos, espaços e variantes comuns nos nomes das categorias.
        import unicodedata
        category=" ".join(unicodedata.normalize("NFKD", (c["category"] or ""))
                           .encode("ascii", "ignore").decode("ascii").casefold().split())
        aliases={
            "rainha do samba":"rainha do samba",
            "diva do samba plus size":"diva do samba (plus size)",
            "diva do samba (plus size)":"diva do samba (plus size)",
            "passista de ouro":"passista de ouro",
        }
        if category not in aliases:
            continue
        # O Concurso consulta diretamente as notas da Audição no banco.
        # A publicação do relatório detalhado não pode ocultar participantes
        # que tenham notas salvas, por exemplo quando ainda não têm chegada
        # registrada ou quando a categoria não apareceu no relatório.
        ev=by.get(c["id"],{})
        # Only show candidates for whom the audition has at least some actual
        # score data; do not impose a finalist quota or require five judges.
        available=[v for v in ev.values()
                   if v["communication"] is not None or v["samba"] is not None]
        if not available:
            continue
        complete=[v for v in available
                  if v["communication"] is not None and v["samba"] is not None]
        audition_total=round(sum(v["communication"]+v["samba"] for v in complete),1) if complete else None
        audition_samba=round(sum(v["samba"] for v in complete),1) if complete else None
        partial_total=round(sum((v["communication"] or 0)+(v["samba"] or 0) for v in available),1)
        chosen.append({"id":c["id"],"category":c["category"],"name":c["name"],
            "audition_total":audition_total,"audition_samba":audition_samba,
            "audition_partial_total":partial_total,
            "audition_complete_evaluators":len(complete),
            "audition_evaluators_with_data":len(available)})
    # Participants with complete data sort by audition total; those with only
    # partial data remain available after them, instead of being omitted.
    chosen.sort(key=lambda x:(x["category"].casefold(),
        x["audition_total"] is None,
        -(x["audition_total"] if x["audition_total"] is not None else x["audition_partial_total"]),
        -(x["audition_samba"] or 0),x["name"].casefold()))
    return chosen

def contest_result_rows(audition_con, contest_con):
    eligible=contest_eligible(audition_con)
    scores=contest_con.execute("SELECT candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba FROM contest_scores").fetchall()
    by={}
    for r in scores:
        by.setdefault(r["candidate_id"],{})[r["evaluator"]]={"aesthetics_sympathy":r["aesthetics_sympathy"],"communication_elegance":r["communication_elegance"],"samba":r["samba"]}
    output=[]
    for c in eligible:
        ev=by.get(c["id"],{})
        women=c["category"].strip().casefold()!="passista de ouro"
        required=("communication_elegance","samba","aesthetics_sympathy") if women else ("communication_elegance","samba")
        complete=[v for v in ev.values() if all(v[k] is not None for k in required)]
        # Partial results are available as soon as at least one evaluator has saved all applicable criteria.
        # The total is the sum of the current mean of each criterion; it becomes final after five evaluators.
        vals={k:round(sum(v[k] for v in complete)/len(complete),2) if complete else None for k in required}
        partial_total=round(sum(vals[k] for k in required),2) if complete else None
        final= len(complete)>=5
        output.append({"id":c["id"],"category":c["category"],"name":c["name"],
            "aesthetics_sympathy":vals.get("aesthetics_sympathy"),"communication_elegance":vals.get("communication_elegance"),
            "samba":vals.get("samba"),"total":partial_total,"partial_total":partial_total,
            "complete_evaluators":len(complete),"complete":final,"status":"Final" if final else ("Parcial" if complete else "Aguardando notas"),
            "audition_total":c["audition_total"],"title":None})
    # Only complete contest entries can be ranked; tie-break is higher mean Samba no Pé.
    for cat in ("Passista de Ouro","Diva do Samba (Plus Size)","Rainha do Samba"):
        group=[x for x in output if x["category"].strip().casefold()==cat.casefold() and x["complete"]]
        group.sort(key=lambda x:(-x["total"],-(x["samba"] or 0),x["name"].casefold()))
        for i,x in enumerate(group,1):
            if cat=="Rainha do Samba": x["title"]={1:"Rainha do Carnaval",2:"Primeira Princesa",3:"Segunda Princesa"}.get(i,f"{i}º lugar")
            elif cat=="Diva do Samba (Plus Size)": x["title"]="Diva Plus Size" if i==1 else f"{i}º lugar"
            else: x["title"]={1:"Passista de Ouro",2:"Passista de Prata"}.get(i,f"{i}º lugar")
    output.sort(key=lambda x:(x["category"].casefold(),x["title"] is None,x["title"] or "",x["name"].casefold()))
    return output

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(HERE),**kw)
    def send_json(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)));self.send_header("Cache-Control","no-store")
        self.end_headers();self.wfile.write(body)
    def read_json(self):
        length=int(self.headers.get("content-length","0"))
        if length>1024*1024: raise ValueError("Requisição muito grande.")
        return json.loads(self.rfile.read(length).decode("utf-8"))
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/api/health": return self.send_json({"ok":True,"service":"UESP 2027","databases":{"audition":AUDITION_DB.name,"contest":CONTEST_DB.name}})
        if path=="/api/evaluators":
            stage=(parse_qs(urlparse(self.path).query).get("stage",["AUDICAO"])[0] or "AUDICAO").strip().upper()
            if stage not in ("AUDICAO","CONCURSO"):
                return self.send_json({"ok":False,"error":"Etapa inválida. Use AUDICAO ou CONCURSO."},400)
            database=AUDITION_DB if stage=="AUDICAO" else CONTEST_DB
            with dbconn(database) as con:
                rows=con.execute("SELECT name FROM evaluator_roster WHERE stage=? ORDER BY position,name COLLATE NOCASE",(stage,)).fetchall()
            return self.send_json({"ok":True,"stage":stage,"evaluators":[r["name"] for r in rows]})
        if path=="/api/candidates":
            with dbconn() as con:
                rows=con.execute("SELECT id,category,name FROM candidates ORDER BY category,name COLLATE NOCASE").fetchall()
            return self.send_json({"ok":True,"candidates":[dict(x) for x in rows]})
        if path=="/api/arrivals":
            with dbconn() as con:
                rows=con.execute("SELECT candidate_id,arrived_at,device FROM arrivals").fetchall()
            return self.send_json({"ok":True,"arrivals":{str(r["candidate_id"]):{"arrived_at":r["arrived_at"],"device":r["device"]} for r in rows}})
        if path=="/api/scores":
            with dbconn() as con:
                rows=con.execute("SELECT candidate_id,evaluator,communication,samba,updated_at FROM scores").fetchall()
            scores={}
            for r in rows: scores.setdefault(str(r["candidate_id"]),{})[r["evaluator"]]={"communication":r["communication"],"samba":r["samba"],"updated_at":r["updated_at"]}
            return self.send_json({"ok":True,"scores":scores})
        if path=="/api/contest-candidates":
            with dbconn(AUDITION_DB) as audition_con, dbconn(CONTEST_DB) as contest_con:
                candidates=contest_eligible(audition_con)
                rs=contest_con.execute("SELECT candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba,updated_at FROM contest_scores").fetchall()
            saved={}
            for r in rs:
                saved.setdefault(str(r["candidate_id"]),{})[r["evaluator"]]={"aesthetics_sympathy":r["aesthetics_sympathy"],"communication_elegance":r["communication_elegance"],"samba":r["samba"],"updated_at":r["updated_at"]}
            return self.send_json({"ok":True,"candidates":candidates,"scores":saved})
        if path=="/api/contest-results":
            with dbconn(AUDITION_DB) as audition_con, dbconn(CONTEST_DB) as contest_con:
                results=contest_result_rows(audition_con,contest_con)
            return self.send_json({"ok":True,"results":results})
        if path=="/api/results":
            with dbconn() as con:
                cs=con.execute("""
                    SELECT c.id,c.category,c.name
                    FROM candidates c
                    INNER JOIN arrivals a ON a.candidate_id=c.id
                    ORDER BY c.category,c.name COLLATE NOCASE
                """).fetchall()
                rs=con.execute("""
                    SELECT candidate_id,evaluator,communication,samba,updated_at
                    FROM scores
                """).fetchall()
            by={}
            for r in rs:
                by.setdefault(r["candidate_id"],{})[r["evaluator"]]={
                    "communication":r["communication"],
                    "samba":r["samba"],
                    "updated_at":r["updated_at"]
                }
            out=[]
            for c in cs:
                ev=by.get(c["id"],{})
                complete=[
                    v for v in ev.values()
                    if v["communication"] is not None and v["samba"] is not None
                ]
                partial_total=round(sum(v["communication"]+v["samba"] for v in complete),1)
                count=len(complete)
                out.append({
                    "id":c["id"],
                    "category":c["category"],
                    "name":c["name"],
                    "complete_evaluators":count,
                    "complete":count>=5,
                    "partial_total":partial_total if count else None,
                    "final_total":partial_total if count>=5 else None,
                    "status":"Completa" if count>=5 else ("Parcial" if count else "Aguardando notas")
                })
            return self.send_json({"ok":True,"results":out})
        return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path
        if path=="/api/evaluators":
            try:
                data=self.read_json()
            except Exception as e:
                return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            stage=str(data.get("stage","AUDICAO") or "AUDICAO").strip().upper()
            if stage not in ("AUDICAO","CONCURSO"):
                return self.send_json({"ok":False,"error":"Etapa inválida. Use AUDICAO ou CONCURSO."},400)
            names=data.get("evaluators",[])
            if not isinstance(names,list) or any(not isinstance(n,str) for n in names):
                return self.send_json({"ok":False,"error":"Informe uma lista de nomes de avaliadores."},400)
            normalized=[]
            seen=set()
            for item in names:
                name=" ".join(item.split())
                key=name.casefold()
                if not name or key in seen:
                    continue
                if len(name)>80:
                    return self.send_json({"ok":False,"error":"Cada nome pode ter no máximo 80 caracteres."},400)
                seen.add(key)
                normalized.append(name)
            database=AUDITION_DB if stage=="AUDICAO" else CONTEST_DB
            with dbconn(database) as con:
                con.execute("DELETE FROM evaluator_roster WHERE stage=?",(stage,))
                con.executemany("INSERT INTO evaluator_roster(stage,name,position) VALUES(?,?,?)",[(stage,name,i) for i,name in enumerate(normalized)])
                con.commit()
            return self.send_json({"ok":True,"stage":stage,"evaluators":normalized})
        if path=="/api/clear-scores":
            # Os nomes dos avaliadores são armazenados na própria tabela scores.
            # Esta operação remove avaliações e nomes, preservando candidatos e chegadas.
            try:
                with dbconn() as con:
                    deleted=con.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
                    con.execute("DELETE FROM scores")
                    con.commit()
                return self.send_json({"ok":True,"deleted":deleted,"message":"Notas e registros de avaliadores excluídos. Candidatos e chegadas foram preservados."})
            except Exception as e:
                return self.send_json({"ok":False,"error":"Não foi possível excluir as notas: "+str(e)},500)
        if path=="/api/publish-audition-results":
            try:
                data=self.read_json()
                participants=data.get("participants", [])
                if not isinstance(participants, list):
                    return self.send_json({"ok":False,"error":"Lista de resultados inválida."},400)
                now=datetime.now().astimezone().isoformat(timespec="seconds")
                published=[]
                with dbconn() as con:
                    for item in participants:
                        if not isinstance(item, dict):
                            continue
                        cid=item.get("candidate_id")
                        if not isinstance(cid,int):
                            continue
                        row=con.execute("SELECT id,category FROM candidates WHERE id=?",(cid,)).fetchone()
                        if not row:
                            continue
                        cat=(row["category"] or "").strip()
                        if cat.casefold() not in ("rainha do samba","diva do samba (plus size)","passista de ouro"):
                            continue
                        # Só publica participantes que tenham ao menos uma nota de Audição.
                        has_score=con.execute("SELECT 1 FROM scores WHERE candidate_id=? AND (communication IS NOT NULL OR samba IS NOT NULL) LIMIT 1",(cid,)).fetchone()
                        if not has_score:
                            continue
                        try: position=max(1,int(item.get("position",len(published)+1)))
                        except (TypeError,ValueError): position=len(published)+1
                        published.append((cid,cat,position,now))
                    con.execute("DELETE FROM audition_result_publication")
                    con.executemany("INSERT INTO audition_result_publication(candidate_id,category,position,published_at) VALUES(?,?,?,?)",published)
                    con.commit()
                return self.send_json({"ok":True,"published":len(published),"published_at":now,"message":"Resultado da Audição disponibilizado para avaliação do Concurso."})
            except Exception as e:
                return self.send_json({"ok":False,"error":"Não foi possível disponibilizar os resultados da Audição: "+str(e)},500)
        if path=="/api/contest-score":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            cid=data.get("candidate_id"); evaluator=str(data.get("evaluator","")).strip()
            aesthetics=data.get("aesthetics_sympathy"); communication=data.get("communication_elegance"); samba=data.get("samba")
            if not isinstance(cid,int): return self.send_json({"ok":False,"error":"Participante inválido."},400)
            if not evaluator or len(evaluator)>80: return self.send_json({"ok":False,"error":"Informe o nome do avaliador (até 80 caracteres)."},400)
            def valid_contest(v):
                if v is None or v=="": return True
                try:
                    n=float(v); return 9<=n<=10 and abs(round(n*10)-n*10)<1e-7
                except Exception: return False
            if not all(valid_contest(v) for v in (aesthetics,communication,samba)):
                return self.send_json({"ok":False,"error":"Notas de 9,0 a 10,0 em intervalos de 0,1."},400)
            aesthetics=float(aesthetics) if aesthetics not in (None,"") else None
            communication=float(communication) if communication not in (None,"") else None
            samba=float(samba) if samba not in (None,"") else None
            with dbconn(AUDITION_DB) as audition_con, dbconn(CONTEST_DB) as contest_con:
                eligible={x["id"]:x for x in contest_eligible(audition_con)}
                if cid not in eligible: return self.send_json({"ok":False,"error":"Participante não está entre os classificados para o concurso. Confira se a Audição tem notas salvas."},400)
                if eligible[cid]["category"].strip().casefold()=="passista de ouro": aesthetics=None
                now=datetime.now().astimezone().isoformat(timespec="seconds")
                contest_con.execute("""INSERT INTO contest_scores(candidate_id,evaluator,aesthetics_sympathy,communication_elegance,samba,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(candidate_id,evaluator) DO UPDATE SET
                    aesthetics_sympathy=excluded.aesthetics_sympathy,communication_elegance=excluded.communication_elegance,
                    samba=excluded.samba,updated_at=excluded.updated_at""",(cid,evaluator,aesthetics,communication,samba,now));contest_con.commit()
            return self.send_json({"ok":True,"updated_at":now})
        if path=="/api/score":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            cid=data.get("candidate_id"); evaluator=str(data.get("evaluator","")).strip()
            communication=data.get("communication"); samba=data.get("samba")
            if not isinstance(cid,int): return self.send_json({"ok":False,"error":"Candidata inválida."},400)
            if not evaluator or len(evaluator)>80: return self.send_json({"ok":False,"error":"Informe o nome do avaliador (até 80 caracteres)."},400)
            def valid(v):
                if v is None or v=="": return True
                try:
                    n=float(v); return 9<=n<=10 and abs(round(n*10)-n*10)<1e-7
                except Exception: return False
            if not valid(communication) or not valid(samba): return self.send_json({"ok":False,"error":"Notas de 9,0 a 10,0 em intervalos de 0,1."},400)
            communication=float(communication) if communication not in (None,"") else None
            samba=float(samba) if samba not in (None,"") else None
            with dbconn() as con:
                if not con.execute("SELECT id FROM candidates WHERE id=?",(cid,)).fetchone(): return self.send_json({"ok":False,"error":"Candidata não encontrada."},404)
                now=datetime.now().astimezone().isoformat(timespec="seconds")
                con.execute("INSERT INTO scores(candidate_id,evaluator,communication,samba,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(candidate_id,evaluator) DO UPDATE SET communication=excluded.communication,samba=excluded.samba,updated_at=excluded.updated_at",(cid,evaluator,communication,samba,now)); con.commit()
            return self.send_json({"ok":True,"updated_at":now})
        if path=="/api/arrival":
            try: data=self.read_json()
            except Exception as e: return self.send_json({"ok":False,"error":"Dados inválidos: "+str(e)},400)
            cid=data.get("candidate_id")
            if not isinstance(cid,int): return self.send_json({"ok":False,"error":"Candidato inválido."},400)
            present=bool(data.get("present"))
            with dbconn() as con:
                exists=con.execute("SELECT id FROM candidates WHERE id=?",(cid,)).fetchone()
                if not exists:return self.send_json({"ok":False,"error":"Candidato não encontrado."},404)
                if present:
                    now=datetime.now().astimezone().isoformat(timespec="seconds")
                    con.execute("INSERT INTO arrivals(candidate_id,arrived_at,device) VALUES(?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET arrived_at=excluded.arrived_at,device=excluded.device",(cid,now,data.get("device","Recepção")))
                    row=con.execute("SELECT candidate_id,arrived_at,device FROM arrivals WHERE candidate_id=?",(cid,)).fetchone()
                    con.commit()
                    return self.send_json({"ok":True,"arrival":{"arrived_at":row["arrived_at"],"device":row["device"]}})
                con.execute("DELETE FROM arrivals WHERE candidate_id=?",(cid,));con.commit()
                return self.send_json({"ok":True,"arrival":None})
        if path!="/api/import": return self.send_json({"ok":False,"error":"Rota não encontrada"},404)
        ctype=self.headers.get("content-type","")
        try:
            length=int(self.headers.get("content-length","0"))
            if length<=0 or length>50*1024*1024:return self.send_json({"ok":False,"error":"Arquivo vazio ou maior que 50 MB."},400)
            filename,raw=parse_multipart(ctype,self.rfile.read(length))
            if not raw:return self.send_json({"ok":False,"error":"Arquivo não recebido."},400)
            import openpyxl
            wb=openpyxl.load_workbook(io.BytesIO(raw),read_only=True,data_only=True);ws=wb[wb.sheetnames[0]]
            rows=[];skipped=0
            for row in ws.iter_rows(min_row=2,values_only=True):
                cat=str(row[0]).strip() if len(row)>0 and row[0] is not None else ""
                name=str(row[1]).strip() if len(row)>1 and row[1] is not None else ""
                if not cat and not name:continue
                if not cat or not name:skipped+=1;continue
                rows.append((cat,name))
            wb.close()
            if not rows:return self.send_json({"ok":False,"error":"Nenhum candidato válido nas colunas A e B."},400)
            with dbconn() as con:
                # Reimportação substitui candidatos e limpa presenças antigas para evitar associação incorreta.
                con.execute("DELETE FROM arrivals");con.execute("DELETE FROM scores");con.execute("DELETE FROM audition_result_publication");con.execute("DELETE FROM candidates")
                con.executemany("INSERT OR IGNORE INTO candidates(category,name) VALUES (?,?)",rows);con.commit()
                grouped=con.execute("SELECT category,COUNT(*) n FROM candidates GROUP BY category").fetchall()
            return self.send_json({"ok":True,"imported":sum(x["n"] for x in grouped),"counts":{x["category"]:x["n"] for x in grouped},"skipped":skipped})
        except ImportError:return self.send_json({"ok":False,"error":"Falta openpyxl. Execute python -m pip install openpyxl"},500)
        except Exception as e:return self.send_json({"ok":False,"error":"Erro ao importar Excel: "+str(e)},400)
    def log_message(self,fmt,*args):print("%s - %s"%(self.address_string(),fmt%args))

def local_ip():
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:s.connect(("192.0.2.1",80));return s.getsockname()[0]
    except Exception:return "IP-DO-NOTEBOOK"
    finally:s.close()

def init_contest_db():
    with dbconn(CONTEST_DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS contest_scores (
          id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL, evaluator TEXT NOT NULL,
          aesthetics_sympathy REAL, communication_elegance REAL, samba REAL, updated_at TEXT NOT NULL,
          UNIQUE(candidate_id,evaluator))""")
        con.commit()

if __name__=="__main__":
    with dbconn(AUDITION_DB): pass
    init_contest_db()
    ip=local_ip()
    print("\nCentral UESP 2027 iniciada.")
    print("No notebook: http://127.0.0.1:8765/central.html")
    print(f"Controle de chegada: http://127.0.0.1:8765/chegada.html")
    print(f"Na rede Wi-Fi: http://{ip}:8765/central.html")
    print(f"Chegada Rainha: http://{ip}:8765/chegada.html?categoria=rainha")
    print(f"Chegada Passista: http://{ip}:8765/chegada.html?categoria=passista")
    print(f"Chegada Diva: http://{ip}:8765/chegada.html?categoria=diva")
    print("Mantenha esta janela aberta. Ctrl+C encerra o servidor.\n")
    ThreadingHTTPServer(("0.0.0.0",8765),Handler).serve_forever()
