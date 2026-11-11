(() => {
  const dialog = document.getElementById('perfume-preview-dialog');
  const frame = document.getElementById('perfume-preview-frame');
  if (!dialog || !frame) return;
  let originLink;
  let position = 0;
  let modalEntry = false;

  function finish() {
    if (!dialog.open) return;
    dialog.close();
    frame.contentWindow.location.replace('about:blank');
    originLink?.focus({preventScroll: true});
    window.scrollTo({top: position, left: 0, behavior: 'instant'});
  }
  function back() {
    if (modalEntry) {
      modalEntry = false;
      history.back();
    } else finish();
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !/^\/perfume\/\d+$/.test(url.pathname)) return;
    event.preventDefault();
    originLink = link;
    position = window.scrollY;
    url.searchParams.set('list_preview', '1');
    frame.contentWindow.location.replace(url.href);
    dialog.showModal();
    history.pushState({perfumePreview: true}, '', location.href);
    modalEntry = true;
  });
  window.addEventListener('popstate', () => {
    modalEntry = false;
    finish();
  });
  document.getElementById('perfume-preview-back').addEventListener('click', back);
  dialog.addEventListener('cancel', event => {event.preventDefault(); back();});
  window.addEventListener('message', event => {
    if (event.origin === location.origin && event.source === frame.contentWindow && event.data === 'close-perfume-preview') back();
  });
})();
