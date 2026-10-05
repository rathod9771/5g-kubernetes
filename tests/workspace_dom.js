// Minimal DOM contract for executing real dependency-free workspace/browser code.
// Intentionally models identity, hierarchy, controls and removal rather than layout.
module.exports=function(){
 const ids=new Map();
 class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.parentElement=null;this.style={};this.dataset={};this.hidden=false;this.attributes={};this.textContent='';this._html='';this.classes=new Set();this.classList={add:x=>this.classes.add(x),remove:x=>this.classes.delete(x),toggle:(x,on)=>{if(on===undefined)on=!this.classes.has(x);on?this.classes.add(x):this.classes.delete(x);},contains:x=>this.classes.has(x)};}
  set id(id){this._id=id;ids.set(id,this);}get id(){return this._id;}
  set className(value){this.classes=new Set(value.split(/\s+/));}get className(){return [...this.classes].join(' ');}
  appendChild(child){child.parentElement=this;this.children.push(child);return child;}
  prepend(child){child.parentElement=this;this.children.unshift(child);}
  set innerHTML(value){for(const child of [...this.children])child.remove();this._html=value;
   for(const match of value.matchAll(/id="([^"]+)"/g)){const child=new Element();child.id=match[1];this.appendChild(child);}}
  get innerHTML(){return this._html;}
  setAttribute(name,value){this.attributes[name]=value;if(name==='id')this.id=value;}
  remove(){for(const child of [...this.children])child.remove();if(this.id&&ids.get(this.id)===this)ids.delete(this.id);if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(c=>c!==this);this.parentElement=null;}
  querySelectorAll(selector){let out=[];for(const child of this.children){if(selector==='iframe'?child.tagName==='IFRAME':selector[0]==='.'?child.classList.contains(selector.slice(1)):false)out.push(child);out.push(...child.querySelectorAll(selector));}return out;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  contains(target){return this===target||this.children.some(c=>c.contains(target));}
  scrollIntoView(){this.focused=true;}
 }
 const document={createElement:tag=>new Element(tag),getElementById:id=>ids.get(id)||null,querySelectorAll:selector=>document.body.querySelectorAll(selector),addEventListener(){},removeEventListener(){}};
 document.body=new Element('body');
 for(const id of ['main','clean-badge','combo-deploy-btn','stack-section','stack-list']){const el=new Element();el.id=id;document.body.appendChild(el);}
 return {document,Element,ids};
};
