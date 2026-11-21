'use strict';

const $ = id => document.getElementById(id);
const money = new Intl.NumberFormat('fr-FR', {style: 'currency', currency: 'EUR'});
const date = new Intl.DateTimeFormat('fr-FR', {dateStyle: 'short', timeStyle: 'short', timeZone: 'Europe/Paris'});
let state = {gifts: []};
let editing = null;
let refreshing = false;

function node(tag, content, className) {
  const element = document.createElement(tag);
  if (content != null) element.textContent = content;
  if (className) element.className = className;
  return element;
}

async function api(path, method = 'GET', payload) {
  const response = await fetch(path, {
    method,
    headers: {'Content-Type': 'application/json', 'X-Cadeaux-App': 'local'},
    body: payload === undefined ? undefined : JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Le serveur local ne répond pas.');
  return data;
}

function notify(message) {
  $('notice').textContent = message || '';
  $('notice').hidden = !message;
}

function resetForm(gift = null) {
  editing = gift?.id ?? null;
  $('form-title').textContent = gift ? 'Modifier le cadeau' : 'Ajouter un cadeau';
  $('save').textContent = gift ? 'Enregistrer' : 'Ajouter à la liste';
  $('cancel').hidden = !gift;
  $('name').value = gift?.name ?? '';
  $('links').value = gift ? gift.sources.map(source => source.link).join('\n') : '';
  $('target').value = gift?.target != null ? gift.target / 100 : '';
  if (gift) $('name').focus();
}

function rank(source) {
  return !source.error && source.offer?.stock === 'InStock' ? source.offer.total ?? Infinity : Infinity;
}

function renderOffer(source, cheapest) {
  const {offer, error} = source;
  const row = node('div', null, 'offer');
  const details = node('div');
  const link = node('a', source.shop);
  link.href = source.link;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  details.append(link);
  if (offer) {
    const stock = {InStock: 'En stock', OutOfStock: 'Épuisé', SoldOut: 'Épuisé', PreOrder: 'Précommande', LimitedAvailability: 'Stock limité'};
    details.append(node('small', [offer.product, offer.seller, stock[offer.stock] || 'Stock à confirmer'].filter(Boolean).join(' · ')));
    if (offer.delivery) details.append(node('small', offer.delivery));
    details.append(node('small', (error ? 'Dernier prix connu · ' : 'Relevé le ') + date.format(new Date(offer.checkedAt))));
  }
  if (error) details.append(node('small', error, 'problem'));
  if (!offer && !error) details.append(node('small', 'En attente du premier relevé'));
  const price = node('div');
  if (offer) {
    const full = offer.total != null;
    if (cheapest) price.append(node('small', 'Meilleur total en stock', 'best'));
    price.append(node('strong', money.format((offer.total ?? offer.price) / 100)));
    price.append(node('small', full
      ? money.format(offer.price / 100) + ' + ' + money.format(offer.shipping / 100) + ' de port'
      : 'Livraison à confirmer'));
  }
  row.append(details, price);
  return row;
}

function renderGift(gift) {
  const card = node('article', null, 'card');
  const head = node('div', null, 'heading');
  const title = node('div');
  title.append(node('h3', gift.name));
  const sorted = [...gift.sources].sort((left, right) => rank(left) - rank(right));
  const best = rank(sorted[0]);
  if (gift.target != null) {
    const reached = best <= gift.target;
    title.append(node('p', 'Budget ' + money.format(gift.target / 100) + (reached ? ' · Objectif atteint' : ''), 'meta'));
  }
  const actions = node('div', null, 'actions');
  const edit = node('button', 'Modifier', 'secondary');
  edit.onclick = () => resetForm(gift);
  const remove = node('button', 'Supprimer', 'secondary');
  remove.onclick = async () => {
    if (!window.confirm('Supprimer « ' + gift.name + ' » de la liste ?')) return;
    try {
      await api('/api/gifts/' + gift.id, 'DELETE');
      if (editing === gift.id) resetForm();
      await refresh();
    } catch (error) { notify(error.message); }
  };
  actions.append(edit, remove);
  head.append(title, actions);
  card.append(head, ...sorted.map(source => renderOffer(source, Number.isFinite(best) && rank(source) === best)));
  return card;
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const wasRunning = state.running;
    state = await api('/api/gifts');
    const {gifts, running, lastRun, nextRun} = state;
    $('collect').disabled = running || !gifts.length;
    $('collect').textContent = running ? 'Relevé en cours…' : 'Vérifier les prix';
    if (wasRunning && !running && $('notice').textContent === 'Cadeau enregistré. Relevé des prix en cours.') notify('');
    $('schedule-info').replaceChildren(
      node('div', 'Prochain relevé · ' + date.format(new Date(nextRun))),
      node('span', lastRun ? 'Dernier relevé · ' + date.format(new Date(lastRun)) : 'Aucun relevé effectué')
    );
    $('count').textContent = gifts.length + (gifts.length === 1 ? ' cadeau' : ' cadeaux');
    const list = $('gifts');
    if (!gifts.length) {
      const empty = node('div', null, 'empty');
      empty.append(node('strong', 'Ta liste est vide'), node('p', 'Ajoute un cadeau et ses liens pour lancer le premier relevé.'));
      list.replaceChildren(empty);
    } else {
      list.replaceChildren(...gifts.map(renderGift));
    }
  } catch (error) { notify(error.message); }
  finally { refreshing = false; }
}

$('gift-form').onsubmit = async event => {
  event.preventDefault();
  $('save').disabled = true;
  notify('');
  try {
    const result = await api(editing ? '/api/gifts/' + editing : '/api/gifts', editing ? 'PUT' : 'POST', {
      name: $('name').value, links: $('links').value.split(/\r?\n/), target: $('target').value
    });
    resetForm();
    notify(result.started ? 'Cadeau enregistré. Relevé des prix en cours.' : 'Cadeau enregistré. Vérifie les prix après le relevé en cours.');
    await refresh();
  } catch (error) { notify(error.message); }
  finally { $('save').disabled = false; }
};

$('cancel').onclick = () => resetForm();
$('collect').onclick = async () => {
  try {
    notify('');
    await api('/api/collect', 'POST');
    await refresh();
  } catch (error) { notify(error.message); }
};

refresh();
setInterval(() => { if (state.running) refresh(); }, 1500);
setInterval(refresh, 30000);
