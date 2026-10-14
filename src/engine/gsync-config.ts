// Google sign-in for Drive sync. The OAuth client ID is public by design (it names the app to Google; it is not a
// secret). Empty: sync is not offered. Tests set window.GSYNC_CLIENT_ID ('' turns sync off) for a stand-in for Google.
export const GOOGLE_CLIENT_ID = '781855983144-1kitbv3ld8unqqor7fnlcrpnq27l8tv2.apps.googleusercontent.com';
export const clientId = (): string => {
  const w = window as any;
  return typeof w.GSYNC_CLIENT_ID === 'string' ? w.GSYNC_CLIENT_ID : GOOGLE_CLIENT_ID;
};
