// Google sign-in for Drive sync. The OAuth client ID is public by design (it names the app to Google; it is not a
// secret). Empty: sync is not offered. Tests set window.GSYNC_CLIENT_ID to use a stand-in for Google.
export const GOOGLE_CLIENT_ID = '';
export const clientId = (): string => ((window as any).GSYNC_CLIENT_ID as string) || GOOGLE_CLIENT_ID;
