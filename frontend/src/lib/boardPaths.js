export const WRITE_PATH = "/board/write";

export function writePath(token) {
  return token ? WRITE_PATH : `/login?next=${encodeURIComponent(WRITE_PATH)}`;
}

export function editPath(postId) {
  return `/board/${postId}/edit`;
}
