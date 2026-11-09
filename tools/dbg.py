import sys,os
sys.path.insert(0,'tools')
import UnityPy
from convert_shader import platform_chunks,entries_of,get_seg,PLATFORM_D3D11
UnityPy.config.FALLBACK_UNITY_VERSION='6000.6.0f1'
e=UnityPy.load('Greymeat/Greymeat_Data/resources.assets')
for o in e.objects:
  if o.type.name!='Shader':continue
  tt=o.read_typetree()
  if tt['m_ParsedForm']['m_Name']!='Greymeat/Tex':continue
  pi=tt['platforms'].index(PLATFORM_D3D11)
  ch=platform_chunks(tt,bytes(tt['compressedBlob']),pi);ents=entries_of(ch)
  p=tt['m_ParsedForm']['m_SubShaders'][0]['m_Passes'][0]
  for prog in('progVertex','progFragment'):
    pr=p[prog];e0=pr['m_PlayerSubPrograms'][3][0] if len(pr['m_PlayerSubPrograms'])>3 and pr['m_PlayerSubPrograms'][3] else None
    print(prog,'subs lens',[len(x) for x in pr['m_PlayerSubPrograms']],'pbi',[x[:3] for x in pr['m_ParameterBlobIndices']],'e0',e0)
    print('common keys',list(pr['m_CommonParameters'].keys()))
    if e0:
      seg=get_seg(ch,ents[e0['m_BlobIndex']]);print('code seg',len(seg),seg[:96].hex())
      pb=pr['m_ParameterBlobIndices'][3][0];seg=get_seg(ch,ents[pb]);print('param seg',len(seg));print(seg[:400].hex());print(seg[:400])
  break
