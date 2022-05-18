CREATE TABLE `acl_groups` (
  `group_name` varchar(256) NOT NULL,
  `who` varchar(256) NOT NULL,
  PRIMARY KEY (`group_name`,`who`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

CREATE TABLE `acls` (
  `who` varchar(256) NOT NULL,
  `command` varchar(256) NOT NULL,
  PRIMARY KEY (`command`,`who`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

INSERT INTO `acls` VALUES ('sysops','addacl'),('users','commands'),('sysops','delacl'),('sysops','groupadd'),('sysops','groupdel'),('users','help'),('sysops','listacls'),('sysops','meet')

CREATE TABLE `aliasses` (
  `command` varchar(256) NOT NULL,
  `is_command` tinyint(1) DEFAULT 0,
  `replacement_text` varchar(256) NOT NULL,
  `nr` int(12) NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`nr`),
  KEY `command` (`command`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
