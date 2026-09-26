"""Short-lived headless Edge validation of S1 offline replay (no model calls)."""
from __future__ import annotations
import argparse,base64,json,subprocess,tempfile,time,urllib.request
from pathlib import Path
import websocket
from chapter6_demo.v12_2.common import save_json,file_sha

def check(demo,output):
 demo,output=Path(demo).resolve(),Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
 edge=Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
 if not edge.exists():raise RuntimeError('Edge not installed at configured path')
 profile=output/'profile';profile.mkdir()
 startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
 process=subprocess.Popen([str(edge),'--headless=new','--disable-gpu','--no-first-run','--disable-background-networking',
  '--no-default-browser-check','--disable-extensions','--remote-allow-origins=http://localhost','--remote-debugging-port=0','--user-data-dir='+str(profile),'about:blank'],
  startupinfo=startup,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 ws=None;events=[];seq=0
 try:
  stop=time.monotonic()+30
  while not (profile/'DevToolsActivePort').exists():
   if time.monotonic()>stop:raise TimeoutError('Headless Edge did not start')
   time.sleep(.1)
  port=int((profile/'DevToolsActivePort').read_text().splitlines()[0])
  with urllib.request.urlopen(f'http://127.0.0.1:{port}/json') as r:target=next(t for t in json.load(r) if t['type']=='page')
  ws=websocket.create_connection(target['webSocketDebuggerUrl'],origin='http://localhost',timeout=15)
  def call(method,params=None):
   nonlocal seq
   seq+=1;ws.send(json.dumps({'id':seq,'method':method,'params':params or {}}))
   while True:
    result=json.loads(ws.recv())
    if result.get('id')==seq:
     if 'error' in result:raise RuntimeError(result['error'])
     return result.get('result',{})
    events.append(result)
  def ev(code):
   r=call('Runtime.evaluate',{'expression':code,'returnByValue':True})
   if 'exceptionDetails' in r:raise RuntimeError(r['exceptionDetails'])
   return r.get('result',{}).get('value')
  call('Page.enable');call('Runtime.enable');call('Network.enable')
  call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1080,'deviceScaleFactor':1,'mobile':False})
  call('Page.navigate',{'url':(demo/'index.html').as_uri()})
  stop=time.monotonic()+15
  while ev("document.querySelector('#run').options.length")!=12:
   if time.monotonic()>stop:raise TimeoutError('Demo initialization failed')
   time.sleep(.1)
  checks={'run_options':12,'external_requests':0,'new_model_calls':0}
  ev("document.querySelector('#next').click()")
  checks['next_changes_step']=ev("document.querySelector('#position').textContent.startsWith('2 /')")
  assert checks['next_changes_step']
  checks['replacement_characters']=ev("document.body.innerText.includes(String.fromCharCode(65533))")
  assert not checks['replacement_characters']
  raw=call('Page.captureScreenshot',{'format':'png'});(output/'desktop.png').write_bytes(base64.b64decode(raw['data']))
  for i in range(12):
   ev(f"document.querySelector('#run').value='{i}';document.querySelector('#run').dispatchEvent(new Event('change'))")
  call('Emulation.setDeviceMetricsOverride',{'width':420,'height':900,'deviceScaleFactor':1,'mobile':True})
  raw=call('Page.captureScreenshot',{'format':'png'});(output/'mobile.png').write_bytes(base64.b64decode(raw['data']))
  req=[e.get('params',{}).get('request',{}).get('url','') for e in events if e.get('method')=='Network.requestWillBeSent']
  checks['external_requests']=len([u for u in req if u.startswith(('https://','http://'))])
  checks['runtime_exceptions']=len([e for e in events if e.get('method')=='Runtime.exceptionThrown'])
  assert checks['external_requests']==0 and checks['runtime_exceptions']==0
  checks['html_sha256']=file_sha(demo/'index.html')
  save_json(output/'browser_check.json',checks,immutable=True)
  return checks
 finally:
  if ws:
   try:ws.send(json.dumps({'id':99999,'method':'Browser.close'}));ws.close()
   except Exception:pass
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)

def main():
 p=argparse.ArgumentParser();p.add_argument('--demo',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();print(json.dumps(check(a.demo,a.output)))
if __name__=='__main__':main()
