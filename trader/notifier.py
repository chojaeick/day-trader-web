import json, os, requests

KAKAO_URL='https://kapi.kakao.com/v2/api/talk/memo/default/send'


def send_kakao_to_me(text: str) -> tuple[bool, str]:
    token=os.getenv('KAKAO_ACCESS_TOKEN','').strip()
    if not token:
        return False, 'KAKAO_ACCESS_TOKEN 미설정'
    template={
        'object_type':'text',
        'text':text[:1900],
        'link': {'web_url':'https://www.kakaopaysec.com','mobile_web_url':'https://www.kakaopaysec.com'},
        'button_title':'확인'
    }
    r=requests.post(KAKAO_URL,
        headers={'Authorization':f'Bearer {token}'},
        data={'template_object':json.dumps(template, ensure_ascii=False)}, timeout=10)
    return r.ok, r.text
