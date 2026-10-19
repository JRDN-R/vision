"""Local installation checks/benchmarks. Never handles user prompts or cloud AI keys."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import time

from local_runtime import LlamaRuntime, digest_file
from owned_assets import exclusive_file


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--action',choices=['check','benchmark'],default='check')
    parser.add_argument('--gpu-layers',type=int,default=0)
    parser.add_argument('--context',type=int,choices=[4096,8192,16384],default=4096)
    parser.add_argument('--threads',type=int,choices=range(1,17))
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    path=Path(args.config).resolve()
    config=json.loads(path.read_text(encoding='utf-8-sig'))
    settings=dict(config.get('localAI') or {},gpuLayers=args.gpu_layers,contextTokens=args.context)
    if args.threads is not None:
        settings['threads']=args.threads
    data=Path(config.get('dataDir') or 'data')
    if not data.is_absolute():data=path.parent/data
    report=dict(schema='vision-local-check-v1',time=time.time(),os=platform.platform(),
                manifestSha256=digest_file(settings['manifestPath']),
                profile={k:settings.get(k) for k in ('gpuLayers','contextTokens','threads','maxOutputTokens')},
                pixelInputValidated=False,defaultActivationReady=False,checks={})
    stage=lambda text: print(text,flush=True)
    from local_standby import maintenance_window
    with maintenance_window(data):
        runtime=LlamaRuntime(settings,lambda:False,stage)
        executable,_=runtime.verify()
        manifest=json.loads(Path(settings['manifestPath']).read_text(encoding='utf-8'))
        report['model']={key:manifest[key] for key in ('modelRepository','modelRevision','modelFile','modelBytes','modelSha256')}
        help_text=subprocess.run([str(executable),'--help'],capture_output=True,text=True,timeout=30,check=True)
        available=help_text.stdout+help_text.stderr
        required=['--parallel','--ctx-size','--api-key','--no-webui','--reasoning-format','--no-context-shift']
        report['checks']['requiredFlags']=all(flag in available for flag in required)
        if not report['checks']['requiredFlags']:raise ValueError('Pinned executable lacks required flags.')
        devices=subprocess.run([str(executable),'--list-devices'],capture_output=True,text=True,timeout=30,check=True)
        # Device inventory contains no account data; keep locally for administrator review.
        report['devices']=(devices.stdout+devices.stderr)[-12000:]
        report['checks']['integrity']=True
        if args.action=='benchmark':
            started=time.monotonic()
            with LlamaRuntime(settings,lambda:False,stage) as live:
                report['loadSeconds']=round(time.monotonic()-started,3)
                messages=[{'role':'system','content':'Answer from the quoted source. Do not expose private reasoning.'},
                          {'role':'user','content':'Source [fixture:1]: RH bore is 0.380 inches. LH bore is 0.379 inches. Do not enlarge either bore. Give both values, preserve the instruction, and cite the source.'}]
                report['inputTokens']=live.count(messages)
                text='';first=None;finish=None
                for event in live.generate(messages,2048):
                    for choice in event.get('choices',[]):
                        content=(choice.get('delta') or {}).get('content') or ''
                        if content and first is None:first=time.monotonic()-started
                        text+=content;finish=choice.get('finish_reason') or finish
                report.update(elapsedSeconds=round(time.monotonic()-started,3),firstAnswerSeconds=first,
                              fixtureAnswer=text,finishReason=finish,peakResidentBytes=live.peak_rss)
                report['checks']['syntheticAnswer']=finish=='stop' and all(v in text for v in ('0.380','0.379','fixture'))
        report['hardwareValidated']=False  # A smoke fixture does not measure safe livestream coexistence.
        Path(args.output).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print('Check saved. Default activation remains blocked pending visual and real-workload acceptance.')
        if args.action=='benchmark' and not report['checks']['syntheticAnswer']:
            raise ValueError('The local smoke-answer check did not pass. Review the saved report.')


if __name__=='__main__':main()
