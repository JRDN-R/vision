import {DatabaseSync} from 'node:sqlite';
export function openJournal(path){
 const database=new DatabaseSync(path,{timeout:15000});
 database.exec("PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS scheduling_operations(operation_key TEXT PRIMARY KEY,actor TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,external_id TEXT)");
 return {close:()=>database.close(),prepare(sql){const statement=database.prepare(sql);return {bind(...values){return {async run(){const result=statement.run(...values);return {meta:{changes:Number(result.changes)}};}};}};}};
}
