(() => {
  const dialog = document.getElementById('master-dialog');
  if (!dialog) return;
  const query = document.getElementById('master-dialog-query');
  const status = document.getElementById('master-dialog-status');
  const results = document.getElementById('master-dialog-results');
  const context = document.getElementById('master-dialog-context');
  const matchForm = document.getElementById('client-matches-form');
  let currentLine = null;
  let generation = 0;
  let controller = null;
  let timer = null;

  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };

  function updateChoices() {
    if (!matchForm) return;
    const selectedInputs = [];
    const outside = [];
    let manualCount = 0;
    matchForm.querySelectorAll('.client-line').forEach(line => {
      const selected = line.querySelector('input.line-choice:checked');
      const manual = line.querySelector('input.manual-choice');
      if (selected) {
        selectedInputs.push(selected);
        manual.checked = false;
      } else {
        if (manual.checked) manualCount++;
        outside.push({index: line.dataset.line, raw: line.dataset.raw, manual: manual.checked});
      }
      line.classList.toggle('manual-line', !selected && manual.checked);
      line.querySelector('.line-selected').textContent = selected ? `Elegiste: ${selected.dataset.productName}` : manual.checked ? 'Se cargará a mano; no se incluye en la selección.' : '';
    });
    const uniqueCount = new Set(selectedInputs.map(input => input.value)).size;
    document.getElementById('client-selected-count').textContent = selectedInputs.length;
    document.getElementById('client-footer-matched').textContent = selectedInputs.length;
    document.getElementById('client-footer-unique').textContent = uniqueCount;
    document.getElementById('client-footer-outside').textContent = outside.length;
    document.getElementById('client-matched-count').textContent = selectedInputs.length;
    document.getElementById('client-unique-count').textContent = uniqueCount;
    document.getElementById('client-manual-count').textContent = manualCount;
    document.getElementById('client-pending-count').textContent = outside.length - manualCount;
    document.getElementById('client-outside-count').textContent = outside.length;
    document.getElementById('client-outside-list-count').textContent = outside.length;
    document.getElementById('client-repeat-note').textContent = `${selectedInputs.length - uniqueCount} renglones repiten perfumes ya elegidos: se incluirán una sola vez en la selección.`;
    const list = document.getElementById('client-outside-list');
    list.replaceChildren();
    outside.forEach(item => {
      const li = element('li');
      const link = element('a', item.raw);
      link.href = `#client-line-${item.index}`;
      li.append(link, document.createTextNode(item.manual ? ' — Cargar a mano' : ' — Pendiente de revisar'));
      list.append(li);
    });
  }

  function choose(item) {
    if (!currentLine) return;
    const choices = currentLine.querySelector('.line-choices');
    let input = [...choices.querySelectorAll('input.line-choice')].find(node => node.value === String(item.id));
    if (!input) {
      const label = element('label', undefined, 'line-choice-label');
      input = document.createElement('input');
      input.type = 'radio';
      input.className = 'line-choice';
      input.name = `match_${currentLine.dataset.line}`;
      input.value = item.id;
      input.dataset.productName = item.name;
      const link = element('a', 'Ver fotos y descripciones ↗');
      link.href = item.detail_url;
      link.target = '_blank';
      link.rel = 'noopener';
      label.append(input, document.createTextNode(` ${item.name} · ${item.brand || 'Sin marca'} · Elegida manualmente `), link);
      choices.append(label);
      currentLine.querySelector('.no-initial-matches')?.remove();
    }
    input.checked = true;
    updateChoices();
    dialog.close();
    input.focus();
  }

  function showResults(data) {
    results.replaceChildren();
    status.textContent = `${data.total_master} perfumes en maestra · ${data.matching_count} resultados · mostrando ${data.items.length}.`;
    if (!data.items.length) {
      results.append(element('p', 'No se encontraron resultados. Probá con una palabra más corta, la marca o revisá la escritura.'));
    }
    for (const item of data.items) {
      const card = element('article', undefined, 'master-result');
      let photo;
      if (item.image_url) {
        photo = document.createElement('img');
        photo.src = item.image_url;
        photo.alt = item.name;
        photo.loading = 'lazy';
        photo.addEventListener('error', () => { photo.hidden = true; });
      } else {
        photo = element('small', 'Sin foto');
      }
      photo.className = 'master-result-photo';
      const info = element('div');
      info.append(element('h3', item.name), element('p', `${item.brand || 'Sin marca'} · ${item.category}`), element('small', `${item.match} · ${item.photo_count} fotos`));
      const actions = element('div', undefined, 'master-result-actions');
      const detail = element('a', 'Ver ficha ↗');
      detail.href = item.detail_url;
      detail.target = '_blank';
      detail.rel = 'noopener';
      actions.append(detail);
      if (currentLine) {
        const select = element('button', 'Elegir coincidencia');
        select.type = 'button';
        select.addEventListener('click', () => choose(item));
        actions.append(select);
      }
      card.append(photo, info, actions);
      results.append(card);
    }
  }

  async function search(ticket) {
    controller = new AbortController();
    results.setAttribute('aria-busy', 'true');
    status.textContent = 'Buscando en tu biblioteca local…';
    try {
      const url = new URL('/api/master/search', location.origin);
      url.searchParams.set('q', query.value.trim());
      const response = await fetch(url, {signal: controller.signal});
      if (!response.ok) throw new Error('search');
      const data = await response.json();
      if (ticket === generation && dialog.open) showResults(data);
    } catch (error) {
      if (error.name !== 'AbortError' && ticket === generation) {
        status.textContent = 'No se pudo consultar la maestra. Verificá que el programa siga abierto.';
      }
    } finally {
      if (ticket === generation) results.setAttribute('aria-busy', 'false');
    }
  }

  function queueSearch(delay = 200) {
    const ticket = ++generation;
    clearTimeout(timer);
    controller?.abort();
    timer = setTimeout(() => search(ticket), delay);
  }

  document.querySelectorAll('.open-master-search').forEach(button => button.addEventListener('click', () => {
    currentLine = button.dataset.line !== undefined ? document.getElementById(`client-line-${button.dataset.line}`) : null;
    context.textContent = currentLine ? `Elegí la coincidencia para: ${currentLine.querySelector('legend').textContent}` : 'Explorá la maestra. Para vincular un perfume a tu lista, abrí este buscador desde ese renglón.';
    query.value = button.dataset.query || '';
    results.replaceChildren();
    dialog.showModal();
    query.focus();
    query.select();
    queueSearch(0);
  }));
  document.querySelectorAll('.clear-line').forEach(button => button.addEventListener('click', () => {
    const line = document.getElementById(`client-line-${button.dataset.line}`);
    line.querySelectorAll('input.line-choice').forEach(input => { input.checked = false; });
    line.querySelector('input.manual-choice').checked = false;
    updateChoices();
  }));
  matchForm?.addEventListener('change', event => {
    if (event.target.matches('input.manual-choice') && event.target.checked) {
      event.target.closest('.client-line').querySelectorAll('input.line-choice').forEach(input => { input.checked = false; });
    }
    updateChoices();
  });
  document.getElementById('master-dialog-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { generation++; clearTimeout(timer); controller?.abort(); });
  query.addEventListener('input', () => queueSearch());
  query.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); queueSearch(0); } });
  updateChoices();
})();
