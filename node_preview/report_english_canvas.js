/* Approved English MarsChain brief. Same verified data as the Chinese report. */
(() => {
  'use strict';
  const {fillRound,drawQr}=window.MarsReportCanvas;
  const C={bg:'#0b1722',ink:'#f1f1ed',white:'#f5f6f2',muted:'#9baebb',
    soft:'#7e96a6',mint:'#83ccc3',line:'#afcbd624'};
  const FONT='Arial, "PingFang SC", "Microsoft YaHei", sans-serif';
  let artwork=null,artPromise=null;
  const prepare=()=>{
    if(artPromise)return artPromise;
    artPromise=new Promise((resolve,reject)=>{
      const image=new Image();image.decoding='async';
      image.onload=()=>{artwork=image;resolve();};
      image.onerror=()=>{artPromise=null;reject(new Error('Report masthead is unavailable'));};
      image.src='assets/mars-masthead-20261029.png?v=219a50ca1ee2';
    });
    return artPromise;
  };
  const render=(canvas,data)=>{
    if(!artwork)throw new Error('Masthead is not ready');
    const l=data.live,d=data.daily;
    const required=['totalPower','perCoin','oneYiOutput','top100Share','price','highestPrice',
      'triggerPrice','circulation','burned','addresses','snapshotTimeText','snapshotUtcText'];
    if(!required.every(key=>typeof l[key]==='string'&&l[key].trim()!==''&&l[key]!=='Pending')){
      throw new Error('Snapshot data is incomplete; keep the last complete image');
    }
    canvas.width=1024;canvas.height=1840;
    const ctx=canvas.getContext('2d');
    ctx.fillStyle=C.bg;ctx.fillRect(0,0,1024,1840);
    const text=(s,x,y,size=24,color=C.ink,weight=400,width=872,align='left')=>{
      const content=String(s??'Pending');ctx.fillStyle=color;ctx.textAlign=align;
      let fitted=size;
      while(true){
        ctx.font=`${weight} ${fitted}px ${FONT}`;
        if(ctx.measureText(content).width<=width||fitted<=15)break;
        fitted=Math.max(15,fitted-1);
      }
      ctx.fillText(content,x,y,width);
    };
    const line=(y,x=76,end=948)=>{
      ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(end,y);
      ctx.strokeStyle=C.line;ctx.lineWidth=1;ctx.stroke();
    };
    const vertical=(x,y,h)=>{
      ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x,y+h);
      ctx.strokeStyle=C.line;ctx.lineWidth=1;ctx.stroke();
    };
    const metric=(label,value,x,y,width,size=34)=>{
      text(label,x,y,21,C.muted,400,width);
      text(value,x,y+47,size,C.ink,500,width);
    };
    const panel=(y,h,live)=>{
      const gradient=ctx.createLinearGradient(48,y,976,y+h);
      gradient.addColorStop(0,live?'#142e34':'#25364e');
      gradient.addColorStop(1,live?'#10212b':'#19283a');
      fillRound(ctx,48,y,928,h,23,gradient,live?'#87c5c347':'#b4c9e35c');
      fillRound(ctx,48,y+26,4,h-52,2,live?C.mint:'#c6d4e6');
    };
    // Retain the approved horizon. English type is drawn as native canvas text.
    ctx.save();ctx.beginPath();ctx.rect(530,0,494,282);ctx.clip();
    const sx=artwork.naturalWidth*.64,sw=artwork.naturalWidth-sx,scale=494/sw;
    ctx.drawImage(artwork,sx,0,sw,artwork.naturalHeight,530,-25,494,artwork.naturalHeight*scale);
    ctx.restore();
    const veil=ctx.createLinearGradient(500,0,800,0);
    veil.addColorStop(0,C.bg);veil.addColorStop(1,'#0b172200');
    ctx.fillStyle=veil;ctx.fillRect(500,0,300,282);
    text('M A R S C H A I N',76,65,24,'#becbd2',500,650);
    text('MARS BRIEF',72,162,83,C.white,700,860);
    text('On-chain, at a glance.',76,213,25,C.muted,400,670);
    const fade=ctx.createLinearGradient(0,235,0,282);
    fade.addColorStop(0,'#0b172200');fade.addColorStop(1,C.bg);
    ctx.fillStyle=fade;ctx.fillRect(0,235,1024,47);

    const top=270,liveHeight=626;panel(top,liveHeight,true);
    text(l.retained?'LAST VALID SNAPSHOT':'LIVE SNAPSHOT',76,top+46,27,C.white,600,410);
    text(`Snapshot  ${l.snapshotTimeText}  UTC+8`,948,top+40,18,C.ink,400,470,'right');
    text(`${l.snapshotUtcText}  UTC`,948,top+68,17,C.muted,400,480,'right');
    line(top+94);
    text('Total network power',76,top+133,22,C.muted,400,560);
    text(l.totalPower,73,top+214,70,C.ink,600,565);
    text('Top 100 power share',948,top+133,21,C.muted,400,270,'right');
    text(l.top100Share,948,top+208,43,C.ink,500,270,'right');
    const divider=top+238;line(divider);
    metric('Est. power for 1 MARS/day',l.perCoin,76,divider+35,408,35);
    metric('Est. daily output per 100M power',l.oneYiOutput,552,divider+35,396,35);
    vertical(512,divider+24,66);
    text('Estimated from current network power and block rewards.',512,divider+111,17,C.muted,400,872,'center');
    const prices=divider+135;line(prices);
    [['Current price',l.price],['Highest price',l.highestPrice],
      ['Oracle trigger price',l.triggerPrice]].forEach(([label,value],i)=>{
      const x=76+i*298;metric(label,value,x,prices+34,270,35);
      if(i<2)vertical(x+273,prices+22,67);
    });
    const totals=prices+104;line(totals);
    [['Circulating supply',l.circulation],['Total burned',l.burned],
      ['Total addresses',l.addresses]].forEach(([label,value],i)=>{
      const x=76+i*298;metric(label,value,x,totals+33,270,30);
      if(i<2)vertical(x+273,totals+22,65);
    });

    const dailyTop=top+liveHeight+34,dailyHeight=654;panel(dailyTop,dailyHeight,false);
    text('DAILY STATISTICS',76,dailyTop+47,27,C.white,600,500);
    text(d.rangeText.replace(' to ','  →  ')+' UTC+8',948,dailyTop+44,17,C.ink,400,490,'right');
    text('Daily cutoff: 08:00 Beijing time / 00:00 UTC',948,dailyTop+73,17,C.muted,400,650,'right');
    line(dailyTop+99);
    metric('Daily power change',d.newPower,76,dailyTop+135,408,44);
    metric('Daily burned',d.burned,552,dailyTop+135,396,43);vertical(512,dailyTop+120,70);
    const minor=dailyTop+223;line(minor);
    [['Daily active addresses',d.activeAddresses],['Daily new addresses',d.newAddresses],
      ['Daily transactions',d.transactions],['Daily transaction volume',d.volume]].forEach(([label,value],i)=>{
      metric(label,value,76+i*218,minor+35,196,29);
    });
    const growth=minor+98;line(growth);
    text('PERIOD GROWTH',76,growth+40,24,C.ink,600,430);
    text('All windows: Beijing time (UTC+8)',948,growth+40,16,C.muted,400,400,'right');
    const xs=[258,505,748],headings=growth+82;
    ['Power change','New addresses','Burned'].forEach((label,i)=>text(label,xs[i],headings,20,C.muted,400,i===2?200:222));
    const rows=headings+18;line(rows);
    d.periods.forEach((p,i)=>{
      const y=rows+43+i*110;text(`${p.days}D`,76,y,29,C.ink,600,150);
      [p.power,p.addresses,p.burned].forEach((v,j)=>text(v,xs[j],y,28,C.ink,500,j===2?200:222));
      text(p.rangeText.replace(' to ','  →  '),76,y+58,16,C.muted,400,p.state?600:872);
      if(p.state&&p.state!==p.rangeText)text(p.state,948,y+58,16,C.muted,400,260,'right');
      if(i===0)line(y+79);
    });
    const footer=dailyTop+dailyHeight+30;line(footer,48,976);
    text('Tracking daily changes on MarsChain.',48,footer+79,23,C.muted,400,720);
    text('www.marschainrank.com',48,footer+120,20,C.muted,400,700);
    text('K = thousand   M = million   B = billion   T = trillion',48,footer+161,16,C.soft,400,720);
    drawQr(ctx,832,footer+27,144);
    text('Scan for live data',904,footer+197,17,'#becbd2',400,180,'center');
    return canvas;
  };
  window.MarsEnglishReport=Object.freeze({prepare,render});
})();
