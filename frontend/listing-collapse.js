// Keep each card's choice when SSE refreshes rebuild the recommendation list.
const collapsedCards=new Map();
export function attachListingCollapse(card,key){
  const top=card.querySelector('.listing-top'),heading=document.createElement('header'),content=document.createElement('div');
  heading.className='listing-card-heading';heading.append(top);content.className='listing-card-content';content.id='listing-content-'+crypto.randomUUID();
  content.append(...card.childNodes);const toggle=document.createElement('button');toggle.type='button';toggle.className='listing-collapse-toggle';toggle.setAttribute('aria-controls',content.id);
  const title=top.querySelector('.listing-title').textContent;
  const update=()=>{const collapsed=collapsedCards.get(key)===true;card.classList.toggle('is-collapsed',collapsed);content.hidden=collapsed;toggle.textContent=collapsed?"⌄ Expand listing":"⌃ Collapse listing";toggle.setAttribute('aria-expanded',String(!collapsed));toggle.setAttribute('aria-label',(collapsed?"Expand":"Collapse")+"Listing: "+title);};
  toggle.onclick=()=>{collapsedCards.set(key,!collapsedCards.get(key));if(collapsedCards.size>200)collapsedCards.delete(collapsedCards.keys().next().value);update();if(card.getBoundingClientRect().top<0)heading.scrollIntoView({block:'nearest'});};
  card.append(heading,content,toggle);update();
}
