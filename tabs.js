const viewNames=['queue','artists'];
function showView(){
  const chosen=location.hash==='#artists'?'artists':'queue';
  for(const name of viewNames){
    const active=name===chosen,tab=document.getElementById(name+'-tab');
    if(active)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current');
    document.getElementById(name+'-view').hidden=!active;
  }
  const title=chosen==='queue'?'Download queue':'Artists';
  document.title='Telegram STL manager · '+title;
}
window.addEventListener('hashchange',showView);showView();
