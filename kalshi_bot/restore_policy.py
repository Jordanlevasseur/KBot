"""Root-only boot restoration of a single dedicated gateway nft table."""
import argparse
import os
import subprocess
from .gateway import load_config, nft_policy, verify_firewall


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--environment',choices=['demo','production'],required=True)
    a=p.parse_args()
    if os.geteuid()!=0:
        p.error('Root is required to restore the gateway OS policy')
    config=load_config('/etc/kalshi-bot/'+a.environment+'-gateway.json')
    table='kalshi_'+a.environment
    found=subprocess.run(['/usr/sbin/nft','list','table','inet',table],capture_output=True)
    text=('delete table inet '+table+'\n' if found.returncode==0 else '')+nft_policy(config)
    subprocess.run(['/usr/sbin/nft','-f','-'],input=text,text=True,check=True)
    verify_firewall(config)
    print('Dedicated gateway kernel policy restored and verified')


if __name__=='__main__':
    main()
