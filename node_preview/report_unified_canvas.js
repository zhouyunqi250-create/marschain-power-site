/* One poster, two time scopes: current snapshot above, settled statistics below. */
(() => {
  'use strict';
  const {fillRound,fitText,drawQr}=window.MarsReportCanvas;
  const FONT='"PingFang SC", "Microsoft YaHei", sans-serif';
  const PALETTE={bg:'#0b1722',heading:'#f5f6f2',ink:'#f1f1ed',muted:'#9baebb',accent:'#becbd2',line:'#afcbd624'};
  let artwork=null,artPromise=null;
  const prepare=()=>{
    if(artPromise)return artPromise;
    artPromise=new Promise((resolve,reject)=>{
      const image=new Image();image.decoding='async';
      image.onload=()=>{artwork=image;resolve();};
      image.onerror=()=>{artPromise=null;reject(new Error('刊头图片暂未加载'));};
      image.src='assets/mars-masthead-20261029.png?v=219a50ca1ee2';
    });
    return artPromise;
  };
  const drawMasthead=ctx=>{
    if(!artwork)throw new Error('刊头图片尚未准备完成');
    // The original artwork stays intact. Its quiet top/bottom margins are
    // framed by this 4:1 placement without stretching the type or the horizon.
    const cropHeight=artwork.naturalWidth/4;
    const cropY=(artwork.naturalHeight-cropHeight)/2;
    ctx.drawImage(artwork,0,cropY,artwork.naturalWidth,cropHeight,0,0,1024,256);
    const edge=ctx.createLinearGradient(0,230,0,256);
    edge.addColorStop(0,'#0b172200');edge.addColorStop(1,PALETTE.bg);
    ctx.fillStyle=edge;ctx.fillRect(0,230,1024,26);
  };
  const renderHeader=canvas=>{
    canvas.width=1024;canvas.height=256;
    const ctx=canvas.getContext('2d');
    ctx.fillStyle=PALETTE.bg;ctx.fillRect(0,0,1024,256);
    drawMasthead(ctx);return canvas;
  };
  const render=(canvas,data)=>{
    const l=data.live,d=data.daily;
    const C=PALETTE;
    canvas.width=1024;canvas.height=1776;
    const ctx=canvas.getContext('2d');
    ctx.fillStyle=C.bg;ctx.fillRect(0,0,1024,1776);
    const glow=ctx.createRadialGradient(1000,0,5,1000,0,850);
    glow.addColorStop(0,'#a5e6d522');glow.addColorStop(1,'#a5e6d500');
    ctx.fillStyle=glow;ctx.fillRect(0,0,1024,900);
    const text=(s,x,y,size=24,color=C.ink,weight=500,align='left',width=900)=>{
      ctx.fillStyle=color;
      fitText(ctx,String(s??'待补齐'),x,y,width,`${weight} ${size}px ${FONT}`,Math.min(size,18),align);
    };
    const line=(x,y,x2,y2)=>{
      ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x2,y2);ctx.strokeStyle=C.line;ctx.lineWidth=1;ctx.stroke();
    };
    const metric=(label,value,x,y,width=396,size=34,gap=44)=>{
      text(label,x,y,22,C.muted,400,'left',width);
      text(value,x,y+gap,size,C.ink,500,'left',width);
    };

    drawMasthead(ctx);
    ctx.save();ctx.translate(0,96);

    // All values in this frame are current readings or current model estimates.
    const liveFill=ctx.createLinearGradient(48,184,976,758);
    liveFill.addColorStop(0,'#102c32');liveFill.addColorStop(1,'#10212b');
    fillRound(ctx,48,184,928,574,22,liveFill,'#87c5c347');
    ctx.save();
    fillRound(ctx,48,184,928,574,22,null,null);ctx.clip();
    const liveHeader=ctx.createLinearGradient(0,184,0,274);
    liveHeader.addColorStop(0,'#9edfd912');liveHeader.addColorStop(1,'#9edfd900');
    ctx.fillStyle=liveHeader;ctx.fillRect(48,184,928,90);ctx.restore();
    fillRound(ctx,48,212,4,518,2,'#7bc8c0');
    text('实时快照',76,230,32,C.heading,600);
    text(`快照时间 ${l.snapshotTimeText}（北京时间）`,948,226,19,C.ink,400,'right',650);
    text(`UTC ${l.snapshotUtcText}`,948,253,17,C.muted,400,'right',650);
    line(76,274,948,274);
    text('全网总算力',76,311,23,C.muted,400);
    text(l.totalPower,73,381,63,C.ink,600,'left',620);
    text('前100名算力占比',948,311,21,C.muted,400,'right',290);
    text(l.top100Share,948,373,40,C.ink,500,'right',260);
    line(76,407,948,407);
    metric('日产1币所需算力',l.perCoin,76,445,396,34,43);
    metric('1亿算力预计日产出',l.oneYiOutput,552,445,396,34,43);
    line(512,430,512,494);
    text('按当前算力与区块奖励估算',512,524,17,C.muted,400,'center',872);
    line(76,547,948,547);
    [['当前价格',l.price],['最高价格',l.highestPrice],['预言机触发价',l.triggerPrice]].forEach(([label,value],i)=>{
      const x=76+i*298;
      text(label,x,582,21,C.muted,400,'left',270);
      text(value,x,623,34,C.ink,500,'left',270);
      if(i<2)line(x+273,568,x+273,630);
    });
    line(76,652,948,652);
    [['流通币量',l.circulation],['累计销毁量',l.burned],['全网地址数',l.addresses]].forEach(([label,value],i)=>{
      const x=76+i*298;
      text(label,x,688,21,C.muted,400,'left',270);
      text(value,x,729,31,C.ink,500,'left',270);
      if(i<2)line(x+273,674,x+273,736);
    });

    // Every daily figure and period row keeps its own completed time window.
    const dailyFill=ctx.createLinearGradient(48,794,976,1424);
    dailyFill.addColorStop(0,'#21324b');dailyFill.addColorStop(1,'#19283a');
    fillRound(ctx,48,794,928,630,22,dailyFill,'#b4c9e35c');
    ctx.save();
    fillRound(ctx,48,794,928,630,22,null,null);ctx.clip();
    const dailyHeader=ctx.createLinearGradient(0,794,0,891);
    dailyHeader.addColorStop(0,'#d8e7fa13');dailyHeader.addColorStop(1,'#d8e7fa00');
    ctx.fillStyle=dailyHeader;ctx.fillRect(48,794,928,97);ctx.restore();
    fillRound(ctx,48,822,4,574,2,'#c6d4e6');
    text('每日统计',76,840,32,C.heading,600);
    text(d.rangeText,948,838,19,C.ink,400,'right',650);
    text('每日统计 · 北京时间 08:00 结算',76,872,18,C.muted,400);
    line(76,891,948,891);
    metric('每日新增算力',d.newPower,76,927,426,46,45);
    metric('每日销毁量',d.burned,552,927,396,46,45);
    line(512,913,512,981);
    line(76,997,948,997);
    const dailyMetrics=[['每日活跃地址',d.activeAddresses],['每日新增地址',d.newAddresses],['每日交易笔数',d.transactions],['每日交易量',d.volume]];
    dailyMetrics.forEach(([label,value],i)=>{
      const x=76+i*218;
      text(label,x,1031,20,C.muted,400,'left',196);
      text(value,x,1072,29,C.ink,500,'left',196);
    });
    line(76,1097,948,1097);
    text('周期增长',76,1137,26,C.ink,600);
    const xs=[258,505,748];
    ['新增算力','新增地址','销毁量'].forEach((s,i)=>text(s,xs[i],1176,21,C.muted));
    line(76,1195,948,1195);
    d.periods.forEach((p,i)=>{
      const y=1236+i*110;
      text(`${p.days}天`,76,y,29,C.ink,600);
      [p.power,p.addresses,p.burned].forEach((v,c)=>text(v,xs[c],y,29,C.ink,500,'left',c===2?200:222));
      text(p.rangeText,76,y+38,18,C.muted,400,'left',p.state?620:864);
      if(p.state)text(p.state,948,y+38,17,C.muted,400,'right',220);
      if(i===0)line(76,y+63,948,y+63);
    });

    line(48,1454,976,1454);
    text('记录每一天的链上变化。',48,1540,24,C.muted,400);
    text('www.marschainrank.com',48,1582,20,C.muted,400);
    drawQr(ctx,832,1480,144);
    text('扫码观看实时数据',904,1650,18,C.accent,400,'center',144);
    ctx.restore();
    return canvas;
  };
  window.MarsUnifiedReport=Object.freeze({render,renderHeader,prepare});
})();
