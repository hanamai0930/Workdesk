package app

import (
	"crypto/sha256"
	"errors"
	"fmt"
)

func (a *App) projectReply(project, source string, remaining int64) ([]byte, error) {
	rows, err := a.query(`SELECT json_extract(c.data,'$.snapshot.answer') AS answer FROM collaboration c
        JOIN tasks t ON t.id=json_extract(c.data,'$.task_id')
        WHERE c.id=? AND c.kind='work_result' AND t.project_id=?
          AND typeof(json_extract(c.data,'$.snapshot.answer'))='text'
          AND length(CAST(json_extract(c.data,'$.snapshot.answer') AS BLOB)) BETWEEN 1 AND ?`, source, project, remaining)
	if err != nil {
		return nil, err
	}
	if len(rows) != 1 {
		return nil, errors.New("回复版本不可用或超出本次范围")
	}
	return []byte(str(rows[0]["answer"])), nil
}
func (a *App) projectReplyBytes(project string, ref projectVersion, remaining int64) (map[string]any, []byte, error) {
	if ref.Entry != "answer" || len(ref.SHA256) != 64 {
		return nil, nil, errors.New("回复版本无效")
	}
	raw, err := a.projectReply(project, ref.Source, remaining)
	if err != nil {
		return nil, nil, err
	}
	if fmt.Sprintf("%x", sha256.Sum256(raw)) != ref.SHA256 {
		return nil, nil, errors.New("已保存的回复版本已经改变")
	}
	rows, err := a.query(projectMaterialRows+" WHERE kind='reply' AND source=?", project, project, project, project, ref.Source)
	if err != nil {
		return nil, nil, err
	}
	if len(rows) != 1 {
		return nil, nil, errors.New("找不到原任务回复")
	}
	rows[0]["sha256"] = ref.SHA256
	return rows[0], raw, nil
}
