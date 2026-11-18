"""Local test encryption and authentication. Production must replace local keys with KMS."""
import base64,hashlib,hmac,json,os,secrets
from pathlib import Path
from cryptography.fernet import Fernet

def canonical(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(x): return hashlib.sha256((x if isinstance(x,bytes) else str(x).encode())).hexdigest()
def password_hash(password,salt=None):
 salt=salt or secrets.token_hex(16)
 return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),210000).hex()
def verify_password(p,h): return hmac.compare_digest(password_hash(p,h.split(':')[0]),h)
class Vault:
 def __init__(self,root):
  p=Path(root)/'.local-key';p.parent.mkdir(parents=True,exist_ok=True)
  if not p.exists():
   fd=os.open(str(p),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
   with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
  self.key=p.read_bytes();self.f=Fernet(self.key)
 def encrypt(self,x):return self.f.encrypt(str(x).encode()).decode()
 def decrypt(self,x):return self.f.decrypt(x.encode()).decode() if x else ''
 def fingerprint(self,org,phone):return hmac.new(self.key,(org+':'+phone).encode(),hashlib.sha256).hexdigest()
def sign(secret,method,path,timestamp,nonce,body):
 msg='\n'.join([method.upper(),path,str(timestamp),nonce,digest(body)])
 return base64.b64encode(hmac.new(secret.encode(),msg.encode(),hashlib.sha256).digest()).decode()
