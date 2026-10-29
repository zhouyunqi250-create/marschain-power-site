(() => {
  const roundedRect = (ctx, x, y, w, h, r) => {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  };
  const fillRound = (ctx, x, y, w, h, r, fill, stroke) => {
    roundedRect(ctx, x, y, w, h, r);
    if (fill) {
      ctx.fillStyle = fill;
      ctx.fill();
    }
    if (stroke) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  };
  const fitText = (ctx, text, x, y, maxWidth, font, minSize = 22, align = 'left') => {
    const match = String(font).match(/(\d+)px/);
    const startSize = match ? Number(match[1]) : 32;
    let size = startSize;
    do {
      ctx.font = font.replace(/\d+px/, `${size}px`);
      if (ctx.measureText(text).width <= maxWidth || size <= minSize) break;
      size -= 2;
    } while (size > minSize);
    ctx.textAlign = align;
    ctx.fillText(text, x, y);
  };
  const QR_ROWS = [
    '11111110000001001011001111111',
    '10000010100010111101001000001',
    '10111010010111101001101011101',
    '10111010001101110011101011101',
    '10111010101100100111001011101',
    '10000010011001000011001000001',
    '11111110101010101010101111111',
    '00000000000111101100100000000',
    '10101010011000110101100010010',
    '11100000110110110000111001001',
    '10110110100001000110010000111',
    '11101100001000010111000010010',
    '01001011110110001111111001011',
    '11000001111001011000111101001',
    '01110111110110111100001101011',
    '11001000000111101101000001010',
    '01101011010110110100111101011',
    '00100101110010110100101001101',
    '10000011110001000110101100011',
    '01111001001010010111011001010',
    '10011011001010001101111110000',
    '00000000110111011001100010111',
    '11111110010100111101101011011',
    '10000010001101101110100011011',
    '10111010100100111100111110010',
    '10111010001001110000100010100',
    '10111010101000100000110111001',
    '10000010011001001111110010010',
    '11111110111100011111110111011',
  ];
  const drawQr = (ctx, x, y, sizePx) => {
    const matrix = QR_ROWS.map((row) => row.split('').map((bit) => bit === '1'));
    const moduleCount = matrix.length;
    const quiet = 4;
    const step = sizePx / (moduleCount + quiet * 2);
    ctx.fillStyle = '#ffffff';
    fillRound(ctx, x, y, sizePx, sizePx, 18, '#ffffff');
    ctx.fillStyle = '#071124';
    matrix.forEach((row, r) => {
      row.forEach((dark, c) => {
        if (!dark) return;
        ctx.fillRect(x + (c + quiet) * step, y + (r + quiet) * step, Math.ceil(step), Math.ceil(step));
      });
    });
  };


window.MarsReportCanvas={fillRound,fitText,drawQr};
})();
