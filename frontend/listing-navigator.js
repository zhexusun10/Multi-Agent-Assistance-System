export class ListingNavigator{
  constructor(root,rail){this.root=root;this.rail=rail;this.cards=[];this.frame=0;root.addEventListener('scroll',()=>this.schedule(),{passive:true});for(const event of ['wheel','touchstart','pointerdown','keydown'])root.addEventListener(event,()=>{this.clicked=false;},{passive:true});window.addEventListener('resize',()=>this.schedule());}
  setCards(cards,{preservePosition=false}={}){const scroll=this.root.scrollTop,active=this.active||0,clicked=this.clicked;this.clicked=false;this.cards=cards;this.rail.replaceChildren();if(!preservePosition)this.root.scrollTop=0;
    cards.forEach((card,index)=>{const b=document.createElement('button');b.type='button';b.textContent=String(index+1);b.title=card.querySelector('h3').textContent;b.setAttribute('aria-label',`No. ${index+1}: ${b.title}`);b.onclick=()=>{this.clicked=true;const top=card.getBoundingClientRect().top-this.root.getBoundingClientRect().top+this.root.scrollTop;this.root.scrollTo({top,behavior:'instant'});this.select(index);card.focus({preventScroll:true});};this.rail.append(b);});
    this.rail.hidden=!cards.length;this.select(preservePosition?Math.min(active,cards.length-1):0);if(preservePosition){this.root.scrollTop=scroll;this.clicked=clicked;}
  }
  select(index){this.active=index;[...this.rail.children].forEach((b,i)=>{b.classList.toggle('active',i===index);if(i===index)b.setAttribute('aria-current','true');else b.removeAttribute('aria-current');});}
  schedule(){if(this.frame)return;this.frame=requestAnimationFrame(()=>{this.frame=0;if(this.clicked||!this.cards.length||this.root.clientHeight===0)return;
    const top=this.root.getBoundingClientRect().top;let index=0;
    for(let i=0;i<this.cards.length;i++)if(this.cards[i].getBoundingClientRect().top<=top+32)index=i;
    if(this.root.scrollTop>0&&this.root.scrollTop+this.root.clientHeight>=this.root.scrollHeight-2)index=this.cards.length-1;this.select(index);
  });}
}
