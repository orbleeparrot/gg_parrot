import qrcode from "qrcode-generator";

// Loaded only when the guide QR is opened. No image service or network request.
export function qrModules(address) {
  const code = qrcode(0, "M");
  code.addData(address, "Byte");
  code.make();
  const count = code.getModuleCount();
  const margin = 4;
  return Array.from({ length: count + margin * 2 }, (_, row) =>
    Array.from({ length: count + margin * 2 }, (_, col) =>
      row >= margin && col >= margin && row < count + margin && col < count + margin
        ? code.isDark(row - margin, col - margin) : false));
}
